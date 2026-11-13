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
| `registry.tf` | ECR repository (immutable tags, scan on push, keeps 1 image), log group, SSM parameter for the Anthropic key |
| `iam.tf` | Execution role (pull image, write logs, read the key), job role (read `uploads/*`, write `out/*`), instance role |
| `batch.tf` | Security group (no inbound), launch template (IMDSv2, 100 GB encrypted disk), GPU compute environment, queue, job definition |
| `build.tf` | CodeBuild project that builds and pushes the image (smallest machine), its role, log group, SSM parameter for `HF_TOKEN` |

## Prerequisites

- AWS CLI signed in (`aws sts get-caller-identity`), region `us-west-1`.
- The **"Running On-Demand G and VT instances"** quota in us-west-1 is at least 4 vCPUs.
- Terraform; `HF_TOKEN` and `ANTHROPIC_API_KEY` in the project's `.env`. Docker isn't needed locally.

## 1. Create the infrastructure

```bash
cd infra
terraform init
terraform apply          # review the plan, then type "yes"
```

State is kept locally in `infra/terraform.tfstate` (git-ignored). Don't delete it: it's how
Terraform knows what it created.

## 2. Store the two keys

Terraform creates both as SSM SecureString parameters (free standard tier) with a placeholder,
and never changes them afterwards, so the real keys never appear in code or Terraform state:

```bash
set -a; source ../.env; set +a
aws ssm put-parameter --name /lifestream/hf-token --type SecureString --overwrite --value "$HF_TOKEN"
aws ssm put-parameter --name /lifestream/anthropic-api-key --type SecureString --overwrite \
  --value "$ANTHROPIC_API_KEY"
```

`HF_TOKEN` is used only by image builds (to download the gated pyannote model). Without the
Anthropic key, jobs fail at the summary step (run them with `--no-llm` to skip it).

## 3. Build and push the worker image (AWS CodeBuild)

The image is about 10-15 GB (PyTorch with CUDA, plus Whisper large-v3, the alignment model and
pyannote baked in). It's built by CodeBuild on an x86 machine inside AWS and pushed straight to
ECR, so nothing large goes through your computer:

```bash
infra/build-image.sh          # from the project root; takes roughly 20-40 minutes
```

The script zips the **last commit** (commit your changes first), uploads it to
`s3://<bucket>/build/source.zip`, starts the `lifestream-worker-image` build and waits for it.
Build logs are in CloudWatch under `/aws/codebuild/lifestream-worker-image`.

Tags can't be overwritten. For a new build, use a new tag and point the job definition at it:

```bash
infra/build-image.sh v2
terraform -chdir=infra apply -var image_tag=v2
```

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
is limited to the specific bucket prefix / repository / log group / key parameter, roles trust only
the expected services, and the compute environment scales to zero.

## Costs

Everything is set to the lowest-cost option:

- **Nothing while idle** except storage: ECR keeps only the newest image (~10-15 GB, about
  $1-1.50 a month), S3 holds your videos and results (old versions are deleted after 1 day),
  logs are kept 30 days. The API key is in SSM Parameter Store's free standard tier.
- No NAT gateway, load balancer or always-on instance. Image scanning is the free basic scan.
- **Per image build**: CodeBuild's smallest machine, about $0.005 per minute (roughly
  $0.10-0.20 per build); nothing while idle.
- **While a job runs**: one g4dn.xlarge (about $0.63 an hour in us-west-1) plus its 100 GB disk.
  Batch terminates the instance a few minutes after the queue empties. `max_vcpus = 4` means at
  most one GPU instance at a time, and a job is stopped after 2 hours.
- Cheaper still, later: Spot instances (about 60-70% less per job) need a separate
  "All G and VT Spot Instance Requests" quota.

## Removing everything

The bucket is versioned and must be emptied first (including old versions), then
`terraform destroy`. The RDS database was created by hand and isn't managed here.
