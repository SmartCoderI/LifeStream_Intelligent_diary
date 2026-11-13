# ECR: the worker image. Tags can't be overwritten, so each build gets a new tag (var.image_tag).

resource "aws_ecr_repository" "worker" {
  name                 = local.repository_name
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS" # AWS-managed key (aws/ecr)
  }
}

resource "aws_ecr_lifecycle_policy" "worker" {
  repository = aws_ecr_repository.worker.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep only the newest image (each is ~10-15 GB, billed per GB-month)"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 1
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_cloudwatch_log_group" "worker" {
  #checkov:skip=CKV_AWS_158:Logs hold progress messages only; the AWS-managed encryption is enough.
  #checkov:skip=CKV_AWS_338:30 days is enough for debugging jobs; kept short to keep cost minimal.
  name              = local.log_group_name
  retention_in_days = var.log_retention_days
}

# The Claude API key for the summary step: an SSM Parameter Store SecureString (standard tier is
# free; Secrets Manager would be $0.40/month). Terraform creates it with a placeholder and never
# touches the value again; put the real key in yourself so it never appears in code or state.
resource "aws_ssm_parameter" "anthropic" {
  #checkov:skip=CKV_AWS_337:AWS-managed key (aws/ssm) is enough for one API key and costs nothing.
  name        = local.anthropic_param_name
  description = "Anthropic API key for the LifeStream worker (value set by hand)"
  type        = "SecureString"
  tier        = "Standard"
  value       = "placeholder-set-me"

  lifecycle {
    ignore_changes = [value]
  }
}
