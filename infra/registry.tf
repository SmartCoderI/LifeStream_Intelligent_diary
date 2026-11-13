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
      description  = "Keep the 5 newest images (each is ~10-15 GB)"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_cloudwatch_log_group" "worker" {
  #checkov:skip=CKV_AWS_158:Logs hold progress messages only; the AWS-managed encryption is enough.
  name              = local.log_group_name
  retention_in_days = var.log_retention_days
}

# The Claude API key for the summary step. Terraform creates the empty secret;
# put the value in yourself (console or CLI) so it never appears in code or Terraform state.
resource "aws_secretsmanager_secret" "anthropic" {
  #checkov:skip=CKV_AWS_149:AWS-managed key (aws/secretsmanager) is enough for one API key.
  name                    = local.anthropic_secret_name
  description             = "Anthropic API key for the LifeStream worker (value set by hand)"
  recovery_window_in_days = 7
}
