# Least-privilege roles. Each job container gets two roles:
#   execution role - used by ECS to start the container: pull the image, write logs, read the API key
#   job role       - used by the worker code: read uploads/*, write out/*  (nothing else)
# The GPU instances get the standard ECS instance role so the ECS agent can join the cluster.
# Batch itself uses its service-linked role (AWSServiceRoleForBatch), which AWS manages.

# ---------- trust: only ECS tasks in this account may assume the container roles ----------
data "aws_iam_policy_document" "ecs_tasks_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

# ---------- execution role ----------
resource "aws_iam_role" "execution" {
  name               = local.execution_role_name
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

data "aws_iam_policy_document" "execution" {
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"] # AWS only allows "*" for this action
    resources = ["*"]
  }
  statement {
    sid       = "PullWorkerImage"
    actions   = ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    resources = [local.repository_arn]
  }
  statement {
    sid     = "WriteWorkerLogs"
    actions = ["logs:CreateLogStream", "logs:PutLogEvents"]
    # the log group itself (CreateLogStream) and its streams (PutLogEvents)
    resources = [local.log_group_arn, "${local.log_group_arn}:*"]
  }
  statement {
    sid       = "ReadAnthropicKey"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [local.anthropic_secret_arn]
  }
}

resource "aws_iam_role_policy" "execution" {
  name   = "start-worker-container"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution.json
}

# ---------- job role ----------
resource "aws_iam_role" "job" {
  name               = local.job_role_name
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

data "aws_iam_policy_document" "job" {
  statement {
    sid       = "ReadUploads"
    actions   = ["s3:GetObject"]
    resources = ["${local.bucket_arn}/uploads/*"]
  }
  statement {
    sid       = "WriteResults"
    actions   = ["s3:PutObject"]
    resources = ["${local.bucket_arn}/out/*"]
  }
}

resource "aws_iam_role_policy" "job" {
  name   = "read-uploads-write-results"
  role   = aws_iam_role.job.id
  policy = data.aws_iam_policy_document.job.json
}

# ---------- GPU instance role ----------
data "aws_iam_policy_document" "ec2_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "${var.project}-batch-instance"
  assume_role_policy = data.aws_iam_policy_document.ec2_trust.json
}

# AWS's managed policy for ECS container instances (register with the cluster, poll for tasks).
# Containers can't use it: IMDS is limited to one network hop (see batch.tf), so they only
# get the execution and job roles above.
resource "aws_iam_role_policy_attachment" "instance_ecs" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

resource "aws_iam_instance_profile" "instance" {
  name = "${var.project}-batch-instance"
  role = aws_iam_role.instance.name
}
