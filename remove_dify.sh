#!/bin/bash

set -e

DIFY_DIR="dify"

echo "🧹 停止並刪除 Dify 所有容器..."
cd $DIFY_DIR/docker || { echo "❌ 找不到 Dify docker 資料夾"; exit 1; }

docker compose down -v --remove-orphans

echo "🗑️ 刪除 Docker volumes（若需要可打開這段）..."
docker volume rm $(docker volume ls -q | grep 'docker_') || true

echo "✅ Docker compose 清理完成"

# (可選) 完全刪除 Dify 專案資料夾
read -p "是否要刪除 Dify 原始碼資料夾？(y/n): " delete_code
if [ "$delete_code" = "y" ]; then
    cd ../..              # 先離開 dify 目錄
    DIFY_ABS=$(realpath "$DIFY_DIR")
    echo "🧨 正在刪除資料夾：$DIFY_ABS"
    rm -rf "$DIFY_ABS"
    echo "🧨 已刪除 dify/ 專案資料夾"
fi

echo "✅ Dify 環境已重置完成"
