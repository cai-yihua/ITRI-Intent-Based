#!/usr/bin/env bash
set -e

# --- 0) 基本變數 --------------------------------------------------
NAME=n8n

# --- 1) 讀取 .env -------------------------------------------------
if [[ -f .env ]]; then
  export $(grep -v '^#' .env | xargs)
fi
N8N_TAG="${N8N_BRANCH:-latest}"

# --- 2) 如果容器已存在，就啟動或略過 ------------------------------
if docker container inspect "$NAME" &>/dev/null; then
  state=$(docker inspect -f '{{.State.Status}}' "$NAME")
  echo "⚠️  Container \"$NAME\" 已存在，略過建立。"
  exit 0
fi

# --- 3) 容器不存在 → 新建 ----------------------------------------
echo "🚀 正在建立 n8n 容器，版本：${N8N_TAG} …"
docker run -it -d \
  --name "$NAME" \
  --restart unless-stopped \
  --env-file ./.env \
  -v n8n_data:/home/node/.n8n \
  -v $PWD/n8n-images:/files \
  -p 5678:5678 \
  "n8nio/n8n:${N8N_TAG}"

echo "✅ n8n ${N8N_TAG} 已在 http://localhost:5678 啟動"
