#!/bin/bash

# ========== 容器和鏡像名稱設定 ==========
NETWORK=itri-net
DEV_IMAGE_NAME="itri-intent-dashboard-dev"
DEV_CONTAINER_NAME="itri-intent-dashboard-dev"
PROD_IMAGE_NAME="itri-intent-dashboard-prod"
PROD_CONTAINER_NAME="itri-intent-dashboard-prod"

# 讀取 .env 內的環境變數
source Dashboard/.env

# 決定部署版本
DEPLOY_VERSION=${VERSION:-PROD}

set -e

echo "=========================================="
echo "正在部署 Dashboard - $DEPLOY_VERSION 版本"
echo "=========================================="

# 停止並移除現有容器
echo "1) 停止並移除現有容器..."
docker stop $DEV_CONTAINER_NAME || true
docker rm $DEV_CONTAINER_NAME || true
docker stop $PROD_CONTAINER_NAME || true
docker rm $PROD_CONTAINER_NAME || true

if [ "$DEPLOY_VERSION" = "DEV" ]; then
  # === DEV 版本 ===
  echo "2) 建置 Development 鏡像..."
  docker build --no-cache \
    -f Dashboard/Dockerfile.dev \
    --build-arg PORT=${FRONTEND_PORT} \
    -t $DEV_IMAGE_NAME ./Dashboard
  
  echo "3) 啟動 Development 容器..."
  docker run -d \
    --name $DEV_CONTAINER_NAME \
    -p ${FRONTEND_PORT}:${FRONTEND_PORT} \
    $DEV_IMAGE_NAME
else
  # === PROD 版本 ===
  echo "2) 建置 Production 鏡像..."
  docker build --no-cache \
    -f Dashboard/Dockerfile.prod \
    --build-arg PORT=${FRONTEND_PORT} \
    --build-arg PROTOCAL=${PROTOCAL} \
    --build-arg HOST=${HOST} \
    --build-arg API_PORT=${API_PORT} \
    --build-arg API_ROOT=${API_ROOT} \
    --build-arg API_VERSION=${API_VERSION} \
    -t $PROD_IMAGE_NAME ./Dashboard
  
  echo "3) 啟動 Production 容器..."
  docker run -d \
    --network $NETWORK \
    --name $PROD_CONTAINER_NAME \
    -p ${FRONTEND_PORT}:${FRONTEND_PORT} \
    --env-file Dashboard/.env \
    $PROD_IMAGE_NAME
fi