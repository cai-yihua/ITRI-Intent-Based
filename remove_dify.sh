#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="$SCRIPT_DIR/dify"

# ① 找 compose 檔
for file in "$TARGET_DIR/docker/docker-compose."{yml,yaml}; do
  [[ -f "$file" ]] && COMPOSE_FILE="$file" && break
done

# ② 推導 project name = compose 檔所在資料夾名稱
PROJECT_NAME=""
if [[ -n "${COMPOSE_FILE:-}" ]]; then
  PROJECT_NAME="$(basename "$(dirname "$COMPOSE_FILE")")"   # ← "docker"
fi

# ③ 停容器、刪 named volumes
if [[ -n "${COMPOSE_FILE:-}" ]]; then
  echo "🐳 停止並移除 Dify 相關容器..."
  sudo docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" down --remove-orphans --volumes
fi

# ④ 刪整個目錄（可能含 root 檔案）
if [[ -d "$TARGET_DIR" ]]; then
  echo "🗑  正在刪除 $TARGET_DIR/ ..."
  sudo rm -rf "$TARGET_DIR"
  echo "✅ 已成功刪除 $TARGET_DIR/"
else
  echo "ℹ️  目錄 $TARGET_DIR/ 不存在，無需刪除"
fi
