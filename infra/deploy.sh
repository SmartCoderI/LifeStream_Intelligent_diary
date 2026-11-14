#!/usr/bin/env bash
# Stage 3 deployment, from the project root:
#
#   infra/deploy.sh                  infrastructure + dashboard server (the GPU worker image is unchanged)
#   infra/deploy.sh --worker v2      also build worker image tag v2 first (infra/build-image.sh) and use it
#   infra/deploy.sh --tag v2         use worker image v2, already built (e.g. after a failed deploy)
#
# Steps: build the worker image (optional) -> terraform apply -> create the API key (first time)
#        -> build the web app -> upload the server code -> rebuild and restart the server (SSM Run Command).
# The server code comes from your working tree (uncommitted changes included); the worker image is
# built from the last commit (see build-image.sh).

set -euo pipefail
cd "$(dirname "$0")/.."

WORKER_TAG=""
USE_TAG=""
case "${1:-}" in
  --worker) WORKER_TAG="${2:?usage: infra/deploy.sh --worker <new image tag>}" ;;
  --tag) USE_TAG="${2:?usage: infra/deploy.sh --tag <existing image tag>}" ;;
esac
out() { terraform -chdir=infra output -raw "$1"; }

# ---- 1. worker image (optional) and infrastructure ----
if [ -n "$WORKER_TAG" ]; then
  infra/build-image.sh "$WORKER_TAG"
  TAG="$WORKER_TAG"
elif [ -n "$USE_TAG" ]; then
  TAG="$USE_TAG"
else
  TAG="$(out image_tag 2>/dev/null || echo v1)"  # keep the image the job definition already uses
fi
terraform -chdir=infra apply -var "image_tag=$TAG"

REGION="$(out region)"
BUCKET="$(out bucket_name)"
INSTANCE="$(out server_instance_id)"
URL="$(out dashboard_url)"
KEY_PARAM="$(out api_key_parameter_name)"
SOURCE_KEY="$(out server_source_key)"

# ---- 2. API key: generated once, stored only in SSM ----
current="$(aws ssm get-parameter --region "$REGION" --name "$KEY_PARAM" --with-decryption \
  --query Parameter.Value --output text)"
if [ "$current" = "placeholder-set-me" ]; then
  new_key="$(openssl rand -hex 24)"
  aws ssm put-parameter --region "$REGION" --name "$KEY_PARAM" --type SecureString --overwrite \
    --value "$new_key" >/dev/null
  echo "Created a new API key (saved in SSM $KEY_PARAM)."
fi

# ---- 3. web app ----
(cd web && npm ci --silent && npm run build --silent)

# ---- 4. server code -> S3 ----
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
COPYFILE_DISABLE=1 tar -czf "$tmp/server.tar.gz" --exclude '__pycache__' --exclude '*.pyc' \
  pyproject.toml uv.lock .python-version README.md src infra/server web/dist
aws s3 cp --region "$REGION" --only-show-errors "$tmp/server.tar.gz" "s3://$BUCKET/$SOURCE_KEY"

# ---- 5. rebuild and restart on the server ----
echo "Waiting for the server to be reachable through SSM..."
for _ in $(seq 1 60); do
  ping="$(aws ssm describe-instance-information --region "$REGION" \
    --filters "Key=InstanceIds,Values=$INSTANCE" --query 'InstanceInformationList[0].PingStatus' --output text)"
  [ "$ping" = "Online" ] && break
  sleep 10
done
[ "$ping" = "Online" ] || { echo "The server isn't registered with SSM (status: $ping)."; exit 1; }

script="set -euo pipefail
aws s3 cp --region $REGION --only-show-errors s3://$BUCKET/$SOURCE_KEY /tmp/server.tar.gz
rm -rf /opt/lifestream/src && mkdir -p /opt/lifestream/src
tar -xzf /tmp/server.tar.gz -C /opt/lifestream/src
BUCKET=$BUCKET REGION=$REGION bash /opt/lifestream/src/infra/server/start.sh"
params="$(python3 -c 'import json,sys; print(json.dumps({"commands": [sys.argv[1]], "executionTimeout": ["3600"]}))' "$script")"
COMMAND="$(aws ssm send-command --region "$REGION" --instance-ids "$INSTANCE" \
  --document-name AWS-RunShellScript --comment "LifeStream deploy" --parameters "$params" \
  --query Command.CommandId --output text)"
echo "Building and starting the server (first deploy takes ~10 minutes)..."
while true; do
  status="$(aws ssm get-command-invocation --region "$REGION" --command-id "$COMMAND" \
    --instance-id "$INSTANCE" --query Status --output text 2>/dev/null || echo Pending)"
  case "$status" in Pending|InProgress|Delayed) sleep 15 ;; *) break ;; esac
done
aws ssm get-command-invocation --region "$REGION" --command-id "$COMMAND" --instance-id "$INSTANCE" \
  --query '[StandardOutputContent, StandardErrorContent]' --output text | tail -20
[ "$status" = "Success" ] || { echo "Deploy failed ($status)."; exit 1; }

echo
echo "Dashboard: $URL"
echo "API key:   aws ssm get-parameter --region $REGION --name $KEY_PARAM --with-decryption --query Parameter.Value --output text"
