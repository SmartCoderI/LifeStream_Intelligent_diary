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
  default     = 365
}

variable "offline_plan" {
  description = "Tests only: skip AWS credential checks so `terraform plan` works offline."
  type        = bool
  default     = false
}
