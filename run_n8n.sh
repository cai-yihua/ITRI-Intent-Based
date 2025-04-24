#!/bin/bash
set -e

# 1) 讀取 .env，取得 N8N_BRANCH
if [[ -f .env ]]; then
  # 只載入沒有註解的行，並 export 到目前 shell
  export $(grep -v '^#' .env | xargs)
fi

# 2) 判斷版本號，若沒設就 fallback 到 latest
N8N_TAG="${N8N_BRANCH:-latest}"

echo "🚀 正在啟動 n8n 容器，版本：${N8N_TAG} ..."

docker run -it -d \
  --name n8n \
  --restart unless-stopped \
  --env-file ./.env \
  -v n8n_data:/home/node/.n8n \
  -p 5678:5678 \
  "docker.n8n.io/n8nio/n8n:${N8N_TAG}"

echo "✅ n8n ${N8N_TAG} 已在 http://localhost:5678 啟動"
