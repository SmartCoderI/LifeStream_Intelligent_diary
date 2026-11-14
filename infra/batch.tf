# AWS Batch: GPU instances start when a job is queued and stop when the queue is empty (min 0 vCPUs).

resource "aws_security_group" "batch" {
  name_prefix = "${var.project}-batch-"
  description = "LifeStream GPU workers: no inbound; outbound HTTPS only (S3, ECR, logs, Anthropic API)"
  vpc_id      = local.vpc_id

  ingress = [] # nothing can connect in

  egress {
    description = "HTTPS to AWS APIs and the Anthropic API"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_launch_template" "batch" {
  name_prefix = "${var.project}-batch-"
  description = "Bigger encrypted disk and IMDSv2 for the GPU workers"

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 1          # containers can't reach the instance role
  }

  block_device_mappings {
    device_name = "/dev/xvda" # root volume of the ECS Amazon Linux 2023 GPU image
    ebs {
      volume_size           = var.root_volume_gib
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }
}

resource "aws_batch_compute_environment" "gpu" {
  name = "${var.project}-gpu"
  type = "MANAGED"

  compute_resources {
    type                = "EC2"
    allocation_strategy = "BEST_FIT_PROGRESSIVE"
    min_vcpus           = 0
    desired_vcpus       = 0
    max_vcpus           = var.max_vcpus
    instance_type       = var.instance_types
    instance_role       = aws_iam_instance_profile.instance.arn
    subnets             = local.subnet_ids
    security_group_ids  = [aws_security_group.batch.id]

    launch_template {
      launch_template_id = aws_launch_template.batch.id
      version            = aws_launch_template.batch.latest_version
    }

    ec2_configuration {
      image_type = "ECS_AL2023_NVIDIA" # ECS-optimized Amazon Linux 2023 with NVIDIA drivers
    }
  }

  lifecycle {
    ignore_changes = [compute_resources[0].desired_vcpus] # Batch changes this as it scales
  }
}

# Spot: the same GPU instances at typically 60-70% less, but AWS can reclaim them (the job is
# then retried, see retry_strategy). Needs the "All G and VT Spot Instance Requests" quota.
# Switch with: terraform apply -var use_spot=true
resource "aws_batch_compute_environment" "gpu_spot" {
  count = var.use_spot ? 1 : 0
  name  = "${var.project}-gpu-spot"
  type  = "MANAGED"

  compute_resources {
    type                = "SPOT"
    allocation_strategy = "SPOT_PRICE_CAPACITY_OPTIMIZED" # lowest price among pools unlikely to be reclaimed
    min_vcpus           = 0
    desired_vcpus       = 0
    max_vcpus           = var.max_vcpus
    instance_type       = var.instance_types
    instance_role       = aws_iam_instance_profile.instance.arn
    subnets             = local.subnet_ids
    security_group_ids  = [aws_security_group.batch.id]

    launch_template {
      launch_template_id = aws_launch_template.batch.id
      version            = aws_launch_template.batch.latest_version
    }

    ec2_configuration {
      image_type = "ECS_AL2023_NVIDIA"
    }
  }

  lifecycle {
    ignore_changes = [compute_resources[0].desired_vcpus]
  }
}

locals {
  # The queue uses one environment. Spot jobs don't fall back to on-demand: Batch would keep them
  # waiting on Spot capacity rather than move them, so it's one or the other.
  queue_compute_environment = var.use_spot ? aws_batch_compute_environment.gpu_spot[0] : aws_batch_compute_environment.gpu
}

resource "aws_batch_job_queue" "gpu" {
  name     = "${var.project}-gpu"
  state    = "ENABLED"
  priority = 1

  compute_environment_order {
    order               = 1
    compute_environment = local.queue_compute_environment.arn
  }
}

resource "aws_batch_job_definition" "worker" {
  name                  = "${var.project}-worker"
  type                  = "container"
  platform_capabilities = ["EC2"]
  propagate_tags        = true

  # Submit with parameters { input = "s3://<bucket>/uploads/<file>", date = "YYYY-MM-DD" }.
  parameters = {
    output = "s3://${local.bucket_name}/out/"
    date   = "today"
  }

  # Retry only when AWS took the instance away (Spot reclaim, host failure); a job that fails on
  # its own (bad video, code error) isn't retried, so it can't burn GPU time three times.
  retry_strategy {
    attempts = 3
    evaluate_on_exit {
      on_status_reason = "Host EC2*"
      action           = "RETRY"
    }
    evaluate_on_exit {
      on_reason = "*"
      action    = "EXIT"
    }
  }

  timeout {
    attempt_duration_seconds = var.job_timeout_seconds
  }

  # Built from known names (not resource attributes) so the plan shows it in full for the tests.
  container_properties = jsonencode({
    image            = "${local.repository_url}:${var.image_tag}"
    command          = ["worker", "Ref::input", "--output", "Ref::output", "--date", "Ref::date"]
    jobRoleArn       = "arn:${local.partition}:iam::${local.account_id}:role/${local.job_role_name}"
    executionRoleArn = "arn:${local.partition}:iam::${local.account_id}:role/${local.execution_role_name}"
    resourceRequirements = [
      { type = "VCPU", value = tostring(var.job_vcpus) },
      { type = "MEMORY", value = tostring(var.job_memory_mib) },
      { type = "GPU", value = "1" },
    ]
    environment = [
      { name = "AWS_DEFAULT_REGION", value = var.region },
      { name = "LLM_MODEL", value = var.llm_model },
      { name = "WHISPER_MODEL", value = var.whisper_model },
      { name = "ASR_BATCH_SIZE", value = tostring(var.asr_batch_size) },
      { name = "PARALLEL_AUDIO", value = tostring(var.parallel_audio) },
    ]
    secrets = [
      { name = "ANTHROPIC_API_KEY", valueFrom = local.anthropic_param_arn },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = local.log_group_name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "worker"
      }
    }
    privileged = false
  })

  depends_on = [
    aws_iam_role_policy.execution, aws_iam_role_policy.job, aws_ecr_repository.worker,
    aws_ssm_parameter.anthropic, aws_cloudwatch_log_group.worker,
  ]
}
