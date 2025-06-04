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
  echo "✅ 已複製 .env.dify 為 .env"
else
  echo "⚠️ 找不到 $ENV_SRC，改用範例檔案"
  if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "✅ 已從 .env.example 建立 .env"
  else
    echo "✅ .env 已存在，略過建立"
  fi
fi

echo "🐳 使用 docker compose 啟動 dify 容器..."
docker compose up -d

echo "✅ Dify 已啟動完畢"
