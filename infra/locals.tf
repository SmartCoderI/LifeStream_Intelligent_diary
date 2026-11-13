# Names and ARNs are built from known values (not read back from created resources), so
# `terraform plan` shows every IAM policy in full and the tests can check them.

data "aws_caller_identity" "current" {
  count = var.account_id == null ? 1 : 0
}

data "aws_partition" "current" {}

data "aws_vpc" "default" {
  count   = var.vpc_id == null ? 1 : 0
  default = true
}

data "aws_subnets" "default" {
  count = var.subnet_ids == null ? 1 : 0
  filter {
    name   = "vpc-id"
    values = [local.vpc_id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

locals {
  account_id = var.account_id != null ? var.account_id : data.aws_caller_identity.current[0].account_id
  partition  = data.aws_partition.current.partition
  vpc_id     = var.vpc_id != null ? var.vpc_id : data.aws_vpc.default[0].id
  subnet_ids = var.subnet_ids != null ? var.subnet_ids : data.aws_subnets.default[0].ids

  bucket_name = "${var.project}-data-${local.account_id}-${var.region}"
  bucket_arn  = "arn:${local.partition}:s3:::${local.bucket_name}"

  repository_name = "${var.project}-worker"
  repository_arn  = "arn:${local.partition}:ecr:${var.region}:${local.account_id}:repository/${local.repository_name}"
  repository_url  = "${local.account_id}.dkr.ecr.${var.region}.amazonaws.com/${local.repository_name}"

  execution_role_name = "${var.project}-worker-execution"
  job_role_name       = "${var.project}-worker-job"

  log_group_name = "/aws/batch/${var.project}-worker"
  log_group_arn  = "arn:${local.partition}:logs:${var.region}:${local.account_id}:log-group:${local.log_group_name}"

  hf_param_name = "/${var.project}/hf-token"
  hf_param_arn  = "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter${local.hf_param_name}"

  build_log_group_name = "/aws/codebuild/${var.project}-worker-image"
  build_log_group_arn  = "arn:${local.partition}:logs:${var.region}:${local.account_id}:log-group:${local.build_log_group_name}"

  anthropic_param_name = "/${var.project}/anthropic-api-key"
  anthropic_param_arn  = "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter${local.anthropic_param_name}"
}
