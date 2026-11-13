#!/usr/bin/env bash
# Build the worker image in AWS CodeBuild and push it to ECR.
#
#   infra/build-image.sh            build the tag the job definition uses (terraform output image_tag)
#   infra/build-image.sh v2         build a new tag; then: terraform -chdir=infra apply -var image_tag=v2
#
# The source is the last commit (git archive HEAD), so commit your changes first.
# ECR tags can't be overwritten: building an existing tag fails at the push step.

set -euo pipefail
cd "$(dirname "$0")/.."

PATHS=(Dockerfile .dockerignore pyproject.toml uv.lock .python-version README.md src)
TAG="${1:-$(terraform -chdir=infra output -raw image_tag)}"
BUCKET="$(terraform -chdir=infra output -raw bucket_name)"
PROJECT="$(terraform -chdir=infra output -raw build_project)"
REGION="$(terraform -chdir=infra output -raw region)"

if [ -n "$(git status --porcelain -- "${PATHS[@]}")" ]; then
  echo "Note: uncommitted changes in ${PATHS[*]} are NOT included (building $(git rev-parse --short HEAD))."
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
git archive --format=zip -o "$tmp/source.zip" HEAD "${PATHS[@]}"
aws s3 cp --region "$REGION" --only-show-errors "$tmp/source.zip" "s3://$BUCKET/build/source.zip"

BUILD_ID="$(aws codebuild start-build --region "$REGION" --project-name "$PROJECT" \
  --environment-variables-override "name=IMAGE_TAG,value=$TAG,type=PLAINTEXT" \
  --query build.id --output text)"
echo "Building tag $TAG from commit $(git rev-parse --short HEAD): $BUILD_ID"
echo "Logs: https://$REGION.console.aws.amazon.com/codesuite/codebuild/projects/$PROJECT/history?region=$REGION"

last=""
while true; do
  read -r STATUS PHASE < <(aws codebuild batch-get-builds --region "$REGION" --ids "$BUILD_ID" \
    --query 'builds[0].[buildStatus,currentPhase]' --output text)
  if [ "$PHASE" != "$last" ]; then
    echo "  $(date +%H:%M:%S)  $PHASE"
    last="$PHASE"
  fi
  [ "$STATUS" != "IN_PROGRESS" ] && break
  sleep 20
done

echo "Result: $STATUS"
[ "$STATUS" = "SUCCEEDED" ]
