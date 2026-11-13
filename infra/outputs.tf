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
  value = aws_batch_compute_environment.gpu.name
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

output "anthropic_secret_name" {
  value = aws_secretsmanager_secret.anthropic.name
}
