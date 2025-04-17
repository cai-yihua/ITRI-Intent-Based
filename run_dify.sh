#!/bin/bash

DIFY_BRANCH="1.2.0"
DIFY_DIR="dify"

echo "🚀 下載 dify 原始碼（分支：$DIFY_BRANCH）..."
if [ ! -d "$DIFY_DIR" ]; then
  git clone https://github.com/langgenius/dify.git --branch $DIFY_BRANCH
else
  echo "📁 $DIFY_DIR 目錄已存在，略過 clone"
fi

cd "$DIFY_DIR/docker" || exit 1

if [ ! -f ".env" ]; then
  echo "📄 建立 .env 檔案..."
  cp .env.example .env
else
  echo "✅ .env 檔案已存在"
fi

echo "🔧 更新 .env 內的 SANDBOX 環境變數..."
# 更新或加入 SANDBOX 相關變數
sed -i '/^SANDBOX_API_KEY=/d' .env
echo "SANDBOX_API_KEY=dify-sandbox" >> .env

sed -i '/^SANDBOX_GIN_MODE=/d' .env
echo "SANDBOX_GIN_MODE=release" >> .env

sed -i '/^SANDBOX_WORKER_TIMEOUT=/d' .env
echo "SANDBOX_WORKER_TIMEOUT=600" >> .env

sed -i '/^SANDBOX_ENABLE_NETWORK=/d' .env
echo "SANDBOX_ENABLE_NETWORK=true" >> .env

sed -i '/^SANDBOX_HTTP_PROXY=/d' .env
echo "SANDBOX_HTTP_PROXY=http://ssrf_proxy:3128" >> .env

sed -i '/^SANDBOX_HTTPS_PROXY=/d' .env
echo "SANDBOX_HTTPS_PROXY=http://ssrf_proxy:3128" >> .env

sed -i '/^SANDBOX_PORT=/d' .env
echo "SANDBOX_PORT=8194" >> .env

sed -i '/^PLUGIN_S3_USE_PATH_STYLE=/d' .env
echo "PLUGIN_S3_USE_PATH_STYLE=false" >> .env

sed -i '/^PLUGIN_S3_USE_AWS_MANAGED_IAM=/d' .env
echo "PLUGIN_S3_USE_AWS_MANAGED_IAM=false" >> .env

echo "🐳 使用 docker compose 啟動 dify 容器..."
docker compose up -d

echo "✅ Dify 已啟動完畢"
