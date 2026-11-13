# Read by `lifestream eval-coldstart` (terraform output -json).

output "region" {
  value = var.region
}

output "bucket_name" {
  value = aws_s3_bucket.data.bucket
}

output "ecr_repository_url" {
  value = aws_ecr_repository.worker.repository_url
}

output "image" {
  description = "The image the job definition runs. Push exactly this tag."
  value       = "${aws_ecr_repository.worker.repository_url}:${var.image_tag}"
}

output "compute_environment" {
  value = local.queue_compute_environment.name
}

output "job_queue" {
  value = aws_batch_job_queue.gpu.name
}

output "job_definition" {
  value = aws_batch_job_definition.worker.name
}

output "log_group" {
  value = aws_cloudwatch_log_group.worker.name
}

output "anthropic_parameter_name" {
  value = aws_ssm_parameter.anthropic.name
}

output "build_project" {
  value = aws_codebuild_project.image.name
}

output "image_tag" {
  value = var.image_tag
}

# ---------- Stage 3 ----------

output "dashboard_url" {
  description = "The deployed dashboard (HTTPS)."
  value       = "https://${aws_cloudfront_distribution.dashboard.domain_name}"
}

output "server_instance_id" {
  value = aws_instance.server.id
}

output "api_key_parameter_name" {
  value = aws_ssm_parameter.api_key.name
}

output "server_source_key" {
  value = local.server_source_key
}
