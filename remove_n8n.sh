#!/bin/bash

echo "🛑 停止並刪除 n8n 容器..."
docker stop n8n || true
docker rm n8n || true

echo "🧹 刪除 n8n 資料 volume..."
docker volume rm n8n_data || true

echo "✅ n8n 環境已清理完成"
