#!/bin/bash

# 宣告 network
NETWORK=itri-net

set -e

# 1) 先停止並移除容器 (若容器不存在不會中斷腳本)
docker stop itri-intent-dashboard || true
docker rm itri-intent-dashboard || true

# 2) 讀取 .env 內的環境變數
# （若你不想在 build 時使用，則可以只在 run 時指定 --env-file）
source Dashboard/.env

# 3) 重新 build docker image
docker build --no-cache \
  --build-arg PORT=${FRONTEND_PORT} \
  -t itri-intent-dashboard ./Dashboard

# 4) 執行容器並將埠號用 .env 裡的參數帶入
#   - 假設你在 .env 裡有 FRONTEND_PORT=3000
#   - 如果需要前後端同埠，則 -p ${FRONTEND_PORT}:${FRONTEND_PORT}
#   - 如果你只是單純固定內部容器跑 3000 埠，但對外要 3000，就用 -p ${FRONTEND_PORT}:3000
docker run -d --network $NETWORK \
  --name itri-intent-dashboard \
  -p ${FRONTEND_PORT}:${FRONTEND_PORT} \
  itri-intent-dashboard