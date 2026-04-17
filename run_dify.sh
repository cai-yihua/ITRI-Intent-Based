#!/bin/bash

DIFY_DIR="dify"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_SRC="$SCRIPT_DIR/.env.dify"

echo "🚀 下載 dify 原始碼（分支：$DIFY_BRANCH）..."
if [ ! -d "$DIFY_DIR" ]; then
  git clone https://github.com/langgenius/dify.git --branch $DIFY_BRANCH
else
  echo "📁 $DIFY_DIR 目錄已存在，略過 clone"
fi

echo "ENV_SRC $ENV_SRC"
cd "$DIFY_DIR/docker" || exit 1

if [ -f "$ENV_SRC" ]; then
  echo "📄 偵測到自訂環境檔：$ENV_SRC"
  cp "$ENV_SRC" .env
  # 自動生成 SECRET_KEY（替換 placeholder）
  GENERATED_KEY=$(openssl rand -base64 42)
  sed -i "s|SECRET_KEY=PLACEHOLDER_AUTO_GENERATED|SECRET_KEY=${GENERATED_KEY}|" .env
  echo "✅ 已複製 .env.dify 為 .env（SECRET_KEY 已自動生成）"
else
  echo "⚠️ 找不到 $ENV_SRC，改用範例檔案"
  if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "✅ 已從 .env.example 建立 .env"
  else
    echo "✅ .env 已存在，略過建立"
  fi
fi

# 從根目錄 .env 讀取 DIFY_LOGIN_URL，推導 FILES_URL
ROOT_ENV="$SCRIPT_DIR/.env"
if [ -f "$ROOT_ENV" ]; then
  DIFY_LOGIN_URL_VAL=$(grep -E "^DIFY_LOGIN_URL=" "$ROOT_ENV" | cut -d'=' -f2- | tr -d '"'"'"' ')
  FILES_URL_VAL="${DIFY_LOGIN_URL_VAL%/console/api/login}"
  if [ -n "$FILES_URL_VAL" ]; then
    sed -i "s|^FILES_URL=.*|FILES_URL=${FILES_URL_VAL}|" .env
    sed -i "s|^INTERNAL_FILES_URL=.*|INTERNAL_FILES_URL=http://api:5001|" .env
    echo "✅ 已設定 FILES_URL=${FILES_URL_VAL}"
  fi
fi

echo "🐳 使用 docker compose 啟動 dify 容器..."
docker compose up -d

echo "✅ Dify 已啟動完畢"
