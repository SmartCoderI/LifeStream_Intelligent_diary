# F11: a finished upload starts the GPU job. No code: S3 sends "Object Created" to EventBridge,
# a rule picks video files under uploads/ and submits a Batch job with the file as its input.
#
#   s3://<bucket>/uploads/<video_id>/<video_id>.mp4  ->  rule  ->  SubmitJob(input = s3://<bucket>/<key>)
#
# The worker reads the recording date from the object's metadata (set by the dashboard's upload link).

resource "aws_s3_bucket_notification" "data" {
  bucket      = aws_s3_bucket.data.id
  eventbridge = true # free; EventBridge rules pay nothing for AWS service events
}

# The file type check: only these extensions under uploads/ start a job (anything else is ignored).
# EventBridge rejects a rule with 6 "uploads/*<ext>" wildcards as too complex (5 is its limit for
# these patterns), so the extensions are split over rules of 3; each starts the same job.
locals {
  upload_rule_groups = { for i, group in chunklist(local.video_extensions, 3) : tostring(i) => group }
}

resource "aws_cloudwatch_event_rule" "upload" {
  for_each    = local.upload_rule_groups
  name        = "${var.project}-upload-${each.key}"
  description = "Start a LifeStream GPU job when a ${join("/", each.value)} video is uploaded to uploads/"
  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = { name = [local.bucket_name] }
      object = { key = [for ext in each.value : { wildcard = "uploads/*${ext}" }] }
    }
  })
}

resource "aws_cloudwatch_event_target" "upload" {
  for_each = aws_cloudwatch_event_rule.upload
  rule     = each.value.name
  arn      = aws_batch_job_queue.gpu.arn
  role_arn = aws_iam_role.events.arn

  batch_target {
    job_definition = local.job_definition_name # latest active revision
    job_name       = "${var.project}-upload"
  }

  # `Parameters` becomes the job's parameters (Ref::input in the job command).
  # A plain string, not jsonencode: jsonencode would escape the <key> placeholder.
  input_transformer {
    input_paths    = { key = "$.detail.object.key" }
    input_template = "{\"Parameters\": {\"input\": \"s3://${local.bucket_name}/<key>\"}}"
  }

  retry_policy {
    maximum_retry_attempts       = 3
    maximum_event_age_in_seconds = 3600
  }
}

data "aws_iam_policy_document" "events_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "events" {
  name               = "${var.project}-upload-trigger"
  assume_role_policy = data.aws_iam_policy_document.events_trust.json
}

data "aws_iam_policy_document" "events" {
  statement {
    sid       = "SubmitWorkerJob"
    actions   = ["batch:SubmitJob"]
    resources = concat([local.job_queue_arn], local.job_definition_arns)
  }
}

resource "aws_iam_role_policy" "events" {
  name   = "submit-worker-job"
  role   = aws_iam_role.events.id
  policy = data.aws_iam_policy_document.events.json
}
