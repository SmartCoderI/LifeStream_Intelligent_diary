#!/usr/bin/env bash
# Runs ON the dashboard server (as root, via SSM Run Command from infra/deploy.sh):
# builds the app image from the unpacked source and (re)starts the two containers.
#
#   BUCKET=... REGION=... bash /opt/lifestream/src/infra/server/start.sh
#
# Data that survives redeploys: /opt/lifestream/data (videos' results, speaker names, run history)
# and the Docker volume lifestream-pg (search database).

set -euo pipefail
: "${BUCKET:?}" "${REGION:?}"
APP=/opt/lifestream
SRC="$APP/src"

cloud-init status --wait >/dev/null || true   # first boot: Docker is installed by user data

param() { aws ssm get-parameter --region "$REGION" --name "$1" --with-decryption --query Parameter.Value --output text; }

# database password: generated once, kept on the server only
if [ ! -f "$APP/db-password" ]; then
  umask 077
  head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' > "$APP/db-password"
fi
DB_PASSWORD="$(cat "$APP/db-password")"

umask 077
cat > "$APP/server.env" <<EOF
S3_BUCKET=$BUCKET
AWS_REGION=$REGION
API_KEY=$(param /lifestream/api-key)
ANTHROPIC_API_KEY=$(param /lifestream/anthropic-api-key)
DATABASE_URL=postgresql://lifestream:$DB_PASSWORD@lifestream-db:5432/lifestream
EOF
umask 022

mkdir -p "$APP/data"
chown 1000:1000 "$APP/data"   # the app container runs as uid 1000

docker network inspect lifestream >/dev/null 2>&1 || docker network create lifestream
if [ -z "$(docker ps -q --filter name=^lifestream-db$)" ]; then
  docker rm -f lifestream-db >/dev/null 2>&1 || true
  docker run -d --name lifestream-db --network lifestream --restart unless-stopped \
    -e POSTGRES_USER=lifestream -e POSTGRES_PASSWORD="$DB_PASSWORD" -e POSTGRES_DB=lifestream \
    -v lifestream-pg:/var/lib/postgresql/data pgvector/pgvector:pg17
fi

echo "Building the app image..."
docker build -q -t lifestream-server -f "$SRC/infra/server/Dockerfile" "$SRC"

docker rm -f lifestream-server >/dev/null 2>&1 || true
docker run -d --name lifestream-server --network lifestream --restart unless-stopped \
  --env-file "$APP/server.env" -p 80:8000 -v "$APP/data:/app/data" lifestream-server
docker image prune -f >/dev/null

for _ in $(seq 1 30); do
  if curl -fsS http://localhost/api/config >/dev/null; then
    echo "Server is up: $(curl -fsS http://localhost/api/config)"
    exit 0
  fi
  sleep 2
done
echo "Server didn't answer; recent logs:"
docker logs --tail 50 lifestream-server
exit 1
