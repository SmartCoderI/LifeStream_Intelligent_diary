variable "region" {
  description = "AWS region for everything (must match the GPU quota and the RDS database)."
  type        = string
  default     = "us-west-1"
}

variable "project" {
  description = "Name prefix for every resource."
  type        = string
  default     = "lifestream"
}

variable "account_id" {
  description = "AWS account ID. Leave null to look it up; set it to plan without credentials (tests)."
  type        = string
  default     = null
}

variable "vpc_id" {
  description = "VPC for the GPU workers. Null = the default VPC."
  type        = string
  default     = null
}

variable "subnet_ids" {
  description = "Subnets for the GPU workers. Null = the default VPC's default subnets (public, so workers reach S3/ECR without a NAT gateway)."
  type        = list(string)
  default     = null
}

variable "instance_types" {
  description = "GPU instance types Batch may launch."
  type        = list(string)
  default     = ["g4dn.xlarge"]
}

variable "use_spot" {
  description = "Run jobs on Spot GPU instances (typically 60-70% cheaper; can be reclaimed, then retried). Needs the Spot G and VT quota."
  type        = bool
  default     = false
}

variable "max_vcpus" {
  description = "Most vCPUs running at once (4 = one g4dn.xlarge). Must fit the 'Running On-Demand G and VT instances' quota."
  type        = number
  default     = 4
}

variable "root_volume_gib" {
  description = "Instance disk: holds the ~10-15 GB worker image (twice while it unpacks) plus job files."
  type        = number
  default     = 100
}

variable "image_tag" {
  description = "Worker image tag in ECR that the job definition runs (tags are immutable: push a new tag per build)."
  type        = string
  default     = "v1"
}

variable "llm_model" {
  description = "Claude model for classification and summaries in Batch jobs (e.g. claude-sonnet-5-5 or claude-haiku-5-5 to cut cost)."
  type        = string
  default     = "claude-opus-5-5"
}

variable "whisper_model" {
  description = "Whisper model the Batch job uses. The image has large-v3 and large-v3-turbo (several times faster to decode; check M1/M3b before switching)."
  type        = string
  default     = "large-v3"

  validation {
    condition     = contains(["large-v3", "large-v3-turbo"], var.whisper_model)
    error_message = "The worker image only contains large-v3 and large-v3-turbo (see EXTRA_WHISPER_MODELS in the Dockerfile)."
  }
}

variable "asr_batch_size" {
  description = "Whisper segments transcribed at once on the GPU (the T4's 16 GB fits 16 with large-v3 float16 and diarization alongside)."
  type        = number
  default     = 16
}

variable "job_vcpus" {
  description = "vCPUs per job (g4dn.xlarge has 4)."
  type        = number
  default     = 4
}

variable "job_memory_mib" {
  description = "Memory per job. g4dn.xlarge has 16 GiB; the ECS agent and OS keep some."
  type        = number
  default     = 14000
}

variable "job_timeout_seconds" {
  description = "Stop a job that runs longer than this."
  type        = number
  default     = 7200
}

variable "log_retention_days" {
  description = "How long worker logs are kept."
  type        = number
  default     = 30
}

variable "offline_plan" {
  description = "Tests only: skip AWS credential checks so `terraform plan` works offline."
  type        = bool
  default     = false
}

# ---------- Stage 3: dashboard server ----------

variable "server_instance_type" {
  description = "Dashboard server (API + web app + search database). Graviton t4g.small: 2 vCPU, 2 GiB, about $12/month."
  type        = string
  default     = "t4g.small"
}

variable "server_volume_gib" {
  description = "Dashboard server disk: Docker images, the search database and processed results."
  type        = number
  default     = 30
}

variable "server_ami_id" {
  description = "AMI for the dashboard server. Null = the latest Amazon Linux 2023 (arm64); set it to plan without credentials (tests)."
  type        = string
  default     = null
}

variable "cloudfront_prefix_list_id" {
  description = "Managed prefix list of CloudFront's origin-facing IPs. Null = look it up; set it to plan without credentials (tests)."
  type        = string
  default     = null
}

variable "extra_cors_origins" {
  description = "Other web origins allowed to upload straight to S3 (e.g. http://localhost:5173 for development)."
  type        = list(string)
  default     = []
}
