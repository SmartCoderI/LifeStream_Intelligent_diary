# Stage 3 dashboard server: one small always-on instance running two containers
# (the FastAPI app, which also serves the built web app, and Postgres + pgvector for search).
# CloudFront in front gives it an HTTPS address (https://<id>.cloudfront.net) without a domain.
#
#   browser --HTTPS--> CloudFront --HTTP, CloudFront IPs only--> EC2 :80 -> container :8000
#
# Code is deployed with infra/deploy.sh (SSM Run Command; no SSH, no inbound port besides 80).

data "aws_ssm_parameter" "al2023_arm" {
  count = var.server_ami_id == null ? 1 : 0
  name  = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

data "aws_ec2_managed_prefix_list" "cloudfront" {
  count = var.cloudfront_prefix_list_id == null ? 1 : 0
  name  = "com.amazonaws.global.cloudfront.origin-facing"
}

locals {
  server_ami                = var.server_ami_id != null ? var.server_ami_id : data.aws_ssm_parameter.al2023_arm[0].insecure_value
  cloudfront_prefix_list_id = var.cloudfront_prefix_list_id != null ? var.cloudfront_prefix_list_id : data.aws_ec2_managed_prefix_list.cloudfront[0].id
}

# ---------- API key (set by infra/deploy.sh on first deploy; never in code or Terraform state) ----------
resource "aws_ssm_parameter" "api_key" {
  #checkov:skip=CKV_AWS_337:AWS-managed key (aws/ssm) is enough for one API key and costs nothing.
  name        = local.api_key_param_name
  description = "API key for the LifeStream dashboard API (value set by infra/deploy.sh)"
  type        = "SecureString"
  tier        = "Standard"
  value       = "placeholder-set-me"

  lifecycle {
    ignore_changes = [value]
  }
}

# ---------- network ----------
resource "aws_security_group" "server" {
  name_prefix = "${var.project}-server-"
  description = "LifeStream dashboard server: HTTP from CloudFront only; outbound HTTPS"
  vpc_id      = local.vpc_id

  ingress {
    description     = "HTTP from CloudFront (viewers connect to CloudFront over HTTPS)"
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    prefix_list_ids = [local.cloudfront_prefix_list_id]
  }

  egress {
    description = "HTTPS to AWS APIs, the Anthropic API and package/image registries"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle {
    create_before_destroy = true
  }
}

# ---------- permissions ----------
data "aws_iam_policy_document" "ec2_trust_server" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "server" {
  name               = local.server_role_name
  assume_role_policy = data.aws_iam_policy_document.ec2_trust_server.json
}

data "aws_iam_policy_document" "server" {
  statement {
    sid       = "SignUploadLinks"
    actions   = ["s3:PutObject"] # the browser's presigned PUT runs with these permissions
    resources = ["${local.bucket_arn}/uploads/*"]
  }
  statement {
    sid       = "ReadVideosAndResults"
    actions   = ["s3:GetObject"]
    resources = ["${local.bucket_arn}/uploads/*", "${local.bucket_arn}/out/*"]
  }
  statement {
    sid       = "DeleteVideos"
    actions   = ["s3:DeleteObject"]
    resources = ["${local.bucket_arn}/uploads/*", "${local.bucket_arn}/out/*"]
  }
  statement {
    sid       = "ListVideos"
    actions   = ["s3:ListBucket"]
    resources = [local.bucket_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["uploads/*", "out/*"]
    }
  }
  statement {
    sid       = "ReadDeploySource"
    actions   = ["s3:GetObject"]
    resources = ["${local.bucket_arn}/${local.server_source_key}"]
  }
  statement {
    sid       = "RetryJobs"
    actions   = ["batch:SubmitJob"]
    resources = concat([local.job_queue_arn], local.job_definition_arns)
  }
  statement {
    sid       = "CancelJobs" # the dashboard's Cancel button
    actions   = ["batch:TerminateJob"]
    resources = ["arn:${local.partition}:batch:${var.region}:${local.account_id}:job/*"]
  }
  statement {
    sid       = "FollowJobs"
    actions   = ["batch:ListJobs", "batch:DescribeJobs"] # AWS only allows "*" for these
    resources = ["*"]
  }
  statement {
    sid       = "ReadKeys"
    actions   = ["ssm:GetParameter"]
    resources = [local.api_key_param_arn, local.anthropic_param_arn]
  }
}

resource "aws_iam_role_policy" "server" {
  name   = "dashboard-server"
  role   = aws_iam_role.server.id
  policy = data.aws_iam_policy_document.server.json
}

# Session Manager / Run Command: how infra/deploy.sh reaches the instance without SSH.
resource "aws_iam_role_policy_attachment" "server_ssm" {
  role       = aws_iam_role.server.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "server" {
  name = local.server_role_name
  role = aws_iam_role.server.name
}

# ---------- the instance ----------
resource "aws_instance" "server" {
  #checkov:skip=CKV_AWS_126:Detailed monitoring costs extra; basic 5-minute metrics are enough.
  #checkov:skip=CKV_AWS_88:Needs a public IP to reach AWS APIs without a NAT gateway; inbound is CloudFront-only.
  #checkov:skip=CKV2_AWS_41:False positive; the instance profile is set below.
  ami                         = local.server_ami
  instance_type               = var.server_instance_type
  subnet_id                   = local.subnet_ids[0]
  vpc_security_group_ids      = [aws_security_group.server.id]
  iam_instance_profile        = aws_iam_instance_profile.server.name
  associate_public_ip_address = true
  ebs_optimized               = true
  monitoring                  = false

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 2          # the app container needs the instance role (one extra hop)
  }

  root_block_device {
    volume_size           = var.server_volume_gib
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  # Docker and a swap file (2 GiB RAM is tight while building the image). The app itself is
  # installed by infra/deploy.sh.
  user_data = <<-EOT
    #!/bin/bash
    set -euxo pipefail
    dnf install -y docker
    systemctl enable --now docker
    if [ ! -f /swapfile ]; then
      dd if=/dev/zero of=/swapfile bs=1M count=2048
      chmod 600 /swapfile && mkswap /swapfile
      echo '/swapfile none swap defaults 0 0' >> /etc/fstab
    fi
    swapon -a
    mkdir -p /opt/lifestream
  EOT

  tags = {
    Name = "${var.project}-server"
  }

  lifecycle {
    ignore_changes = [ami, user_data] # a newer AMI or boot script shouldn't replace the server (and its data)
  }
}

# A fixed address, so CloudFront's origin survives stopping and starting the instance.
resource "aws_eip" "server" {
  domain   = "vpc"
  instance = aws_instance.server.id
}

# ---------- HTTPS: CloudFront in front of the server, nothing cached ----------
resource "aws_cloudfront_distribution" "dashboard" {
  #checkov:skip=CKV_AWS_68:WAF costs at least $5/month; the API key protects the API.
  #checkov:skip=CKV_AWS_86:Access logs aren't needed for a personal dashboard.
  #checkov:skip=CKV_AWS_174:The default *.cloudfront.net certificate can't set a minimum TLS version; it still serves TLS 1.2+.
  #checkov:skip=CKV_AWS_310:One origin; there is nothing to fail over to.
  #checkov:skip=CKV_AWS_374:No geo restriction for a personal dashboard.
  #checkov:skip=CKV2_AWS_32:Responses are the API's JSON and the app's files; no response headers policy needed.
  #checkov:skip=CKV2_AWS_42:A custom certificate needs a domain; the default certificate serves HTTPS.
  #checkov:skip=CKV2_AWS_47:WAF (Log4j rule) costs extra; the app is Python.
  enabled         = true
  comment         = "LifeStream dashboard"
  price_class     = "PriceClass_100" # cheapest: North America and Europe edges
  is_ipv6_enabled = true
  # the app serves its web page for "/" anyway
  default_root_object = "index.html"

  origin {
    origin_id   = "server"
    domain_name = aws_eip.server.public_dns

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
      origin_read_timeout    = 60 # answers to questions can take a while
    }
  }

  default_cache_behavior {
    target_origin_id       = "server"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    # AWS managed policies: CachingDisabled, and AllViewer (pass headers, cookies, query strings)
    cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    origin_request_policy_id = "216adef6-5c7f-47e4-b989-5492eafa07d3"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
