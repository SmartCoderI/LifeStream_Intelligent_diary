# CodeBuild: builds the worker image on an x86 machine inside AWS and pushes it to ECR.
# The smallest machine (2 vCPU, 4 GiB, 64 GB disk) is enough; billed per build minute, $0 while idle.
# Start a build with infra/build-image.sh (uploads the source to s3://<bucket>/build/source.zip).

# The Hugging Face token for downloading the gated pyannote model at build time.
# Free SSM standard tier; Terraform sets a placeholder and never touches the value again.
resource "aws_ssm_parameter" "hf_token" {
  #checkov:skip=CKV_AWS_337:AWS-managed key (aws/ssm) is enough for one read token and costs nothing.
  name        = local.hf_param_name
  description = "Hugging Face read token for building the LifeStream worker image (value set by hand)"
  type        = "SecureString"
  tier        = "Standard"
  value       = "placeholder-set-me"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_cloudwatch_log_group" "build" {
  #checkov:skip=CKV_AWS_158:Build logs only; the AWS-managed encryption is enough.
  #checkov:skip=CKV_AWS_338:30 days is enough for build logs; kept short to keep cost minimal.
  name              = local.build_log_group_name
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "codebuild_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["codebuild.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "build" {
  name               = "${var.project}-image-build"
  assume_role_policy = data.aws_iam_policy_document.codebuild_trust.json
}

data "aws_iam_policy_document" "build" {
  statement {
    sid       = "ReadBuildSource"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${local.bucket_arn}/build/*"]
  }
  statement {
    sid       = "LocateBucket"
    actions   = ["s3:GetBucketAcl", "s3:GetBucketLocation"]
    resources = [local.bucket_arn]
  }
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"] # AWS only allows "*" for this action
    resources = ["*"]
  }
  statement {
    sid = "PushWorkerImage"
    actions = [
      "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage",
    ]
    resources = [local.repository_arn]
  }
  statement {
    sid       = "WriteBuildLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [local.build_log_group_arn, "${local.build_log_group_arn}:*"]
  }
  statement {
    sid       = "ReadHfToken"
    actions   = ["ssm:GetParameters"]
    resources = [local.hf_param_arn]
  }
}

resource "aws_iam_role_policy" "build" {
  name   = "build-and-push-worker-image"
  role   = aws_iam_role.build.id
  policy = data.aws_iam_policy_document.build.json
}

resource "aws_codebuild_project" "image" {
  #checkov:skip=CKV_AWS_316:Privileged mode is required to run Docker inside CodeBuild.
  #checkov:skip=CKV_AWS_147:Artifacts are encrypted with the AWS-managed key; a customer-managed key adds cost.
  name           = "${var.project}-worker-image"
  description    = "Builds the LifeStream GPU worker image and pushes it to ECR"
  service_role   = aws_iam_role.build.arn
  build_timeout  = 90 # minutes
  queued_timeout = 60

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    compute_type    = "BUILD_GENERAL1_SMALL"
    image           = "aws/codebuild/amazonlinux-x86_64-standard:6.0"
    type            = "LINUX_CONTAINER"
    privileged_mode = true # Docker builds

    environment_variable {
      name  = "REPOSITORY_URL"
      value = local.repository_url
    }
    environment_variable {
      name  = "IMAGE_TAG"
      value = var.image_tag # build-image.sh overrides this per build
    }
    environment_variable {
      name  = "HF_TOKEN"
      type  = "PARAMETER_STORE"
      value = local.hf_param_name
    }
  }

  source {
    type     = "S3"
    location = "${local.bucket_name}/build/source.zip"
    buildspec = yamlencode({
      version = "0.2"
      phases = {
        pre_build = {
          commands = [
            "aws ecr get-login-password --region \"$AWS_REGION\" | docker login --username AWS --password-stdin \"$${REPOSITORY_URL%%/*}\"",
          ]
        }
        build = {
          on-failure = "ABORT"
          commands = [
            "DOCKER_BUILDKIT=1 docker build --secret id=hf_token,env=HF_TOKEN -t \"$REPOSITORY_URL:$IMAGE_TAG\" .",
            "docker push \"$REPOSITORY_URL:$IMAGE_TAG\"",
          ]
        }
      }
    })
  }

  logs_config {
    cloudwatch_logs {
      group_name = aws_cloudwatch_log_group.build.name
    }
    s3_logs {
      status = "DISABLED"
    }
  }

  depends_on = [aws_iam_role_policy.build]
}
