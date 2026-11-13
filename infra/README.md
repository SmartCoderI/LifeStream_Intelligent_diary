# F7: cloud infrastructure (Terraform)

One AWS Batch job processes one video on a GPU:

```
s3://<bucket>/uploads/<video>  ->  Batch job (g4dn.xlarge, worker image from ECR)  ->  s3://<bucket>/out/<video_id>/
```

The compute environment has **min 0 vCPUs**, so no GPU instance runs (or costs anything) while the
queue is empty. The first job after idle waits for an instance to launch and pull the image;
that's the cold start M3a measures.

| File | What it creates |
|---|---|
| `storage.tf` | S3 bucket: encrypted (KMS), all public access blocked, ACLs off, HTTPS only, versioned |
| `registry.tf` | ECR repository (immutable tags, scan on push), log group, empty secret for the Anthropic key |
| `iam.tf` | Execution role (pull image, write logs, read the key), job role (read `uploads/*`, write `out/*`), instance role |
| `batch.tf` | Security group (no inbound), launch template (IMDSv2, 100 GB encrypted disk), GPU compute environment, queue, job definition |

## Prerequisites

- AWS CLI signed in (`aws sts get-caller-identity`), region `us-west-1`.
- The **"Running On-Demand G and VT instances"** quota in us-west-1 is at least 4 vCPUs.
- Terraform, Docker; `HF_TOKEN` and `ANTHROPIC_API_KEY` in the project's `.env`.

## 1. Create the infrastructure

```bash
cd infra
terraform init
terraform apply          # review the plan, then type "yes"
```

State is kept locally in `infra/terraform.tfstate` (git-ignored). Don't delete it: it's how
Terraform knows what it created.

## 2. Store the Anthropic API key

Terraform creates the secret empty, so the key never appears in code or Terraform state:

```bash
set -a; source ../.env; set +a
aws secretsmanager put-secret-value --secret-id lifestream/anthropic-api-key \
  --secret-string "$ANTHROPIC_API_KEY"
```

Jobs fail to start until this secret has a value.

## 3. Build and push the worker image

The image is about 10-15 GB (PyTorch with CUDA, plus Whisper large-v3, the alignment model and
pyannote baked in). It must be built for x86 (`linux/amd64`) even on an Apple Silicon Mac.

```bash
cd ..                                            # project root
IMAGE=$(terraform -chdir=infra output -raw image)
aws ecr get-login-password | docker login --username AWS --password-stdin "${IMAGE%%/*}"
set -a; source .env; set +a
docker buildx build --platform linux/amd64 --secret id=hf_token,env=HF_TOKEN -t "$IMAGE" --push .
```

Tags can't be overwritten. For a new build, pick a new tag and point the job definition at it:
`terraform -chdir=infra apply -var image_tag=v2`, then build and push with the new `$IMAGE`.

## 4. Run a job

M3a cold-start check (uploads the video, runs one job, saves a timing report to `data/eval/m3a/`):

```bash
uv run lifestream eval-coldstart path/to/test.mp4 --date 2026-11-12
```

Or by hand:

```bash
BUCKET=$(terraform -chdir=infra output -raw bucket_name)
aws s3 cp video.mp4 "s3://$BUCKET/uploads/video.mp4"
aws batch submit-job --job-name video --job-queue lifestream-gpu --job-definition lifestream-worker \
  --parameters input=s3://$BUCKET/uploads/video.mp4,date=2026-11-12
aws s3 ls "s3://$BUCKET/out/" --recursive
```

Logs: CloudWatch log group `/aws/batch/lifestream-worker`.

## Tests

```bash
uv run pytest tests/infra
```

`terraform validate` and `fmt`, `tflint`, `checkov`, plus checks on the JSON from an offline
`terraform plan` (fake credentials, fixed IDs): the bucket can't be public, every IAM permission
is limited to the specific bucket prefix / repository / log group / secret, roles trust only
the expected services, and the compute environment scales to zero.

## Costs

- **Nothing while idle** except storage: S3 (videos and results), ECR (~10-15 GB per image,
  the 5 newest are kept), the secret (about $0.40 a month).
- **While a job runs**: one g4dn.xlarge (about $0.63 an hour in us-west-1) plus its 100 GB disk.
  Batch terminates the instance a few minutes after the queue empties.

## Removing everything

The bucket is versioned and must be emptied first (including old versions), then
`terraform destroy`. The RDS database was created by hand and isn't managed here.
