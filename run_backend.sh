#!/bin/bash

# 宣告 network
NETWORK=itri-net

function remove_container_if_exists() {
  local container_name="$1"
  if [ "$(docker ps -aq -f name=${container_name})" ]; then
    echo "Stopping and removing existing container: ${container_name}"
    docker stop "${container_name}" 2>/dev/null
    docker rm "${container_name}" 2>/dev/null
  fi
}

remove_container_if_exists "itri-intent-backend"
remove_container_if_exists "intent-postgres-db"
remove_container_if_exists "intent-redis-db"
remove_container_if_exists "intent-pgadmin"


source Backend/.env

# 啟動 Postgres 容器
docker run --network $NETWORK --name intent-postgres-db \
    -e POSTGRES_PASSWORD=$HTTP_POSTGRES_DATABASE_PASSWORD \
    -e POSTGRES_USER=$HTTP_POSTGRES_DATABASE_HOST_USER \
    -p $HTTP_POSTGRES_DATABASE_HOST_PORT:5432 \
    -v ~/postgres_data:/var/lib/postgresql/data \
    -d postgres:latest

sleep 5

# 建立資料庫
docker exec -it intent-postgres-db psql -U $HTTP_POSTGRES_DATABASE_HOST_USER -c "CREATE DATABASE intent;"

# 檢查資料庫列表
# docker exec -it intent-postgres-db psql -U $HTTP_POSTGRES_DATABASE_HOST_USER -l

# 啟動 pgAdmin 容器
docker run --network $NETWORK --name intent-pgadmin \
    -e PGADMIN_DEFAULT_EMAIL=$HTTP_PGADMIN_EMAIL \
    -e PGADMIN_DEFAULT_PASSWORD=$HTTP_PGADMIN_PASSWORD \
    -p $HTTP_PGADMIN_HOST_PORT:80 \
    -d dpage/pgadmin4
  
#啟動 Redis 容器 
docker run --network $NETWORK --name intent-redis-db \
  -e REDIS_USER=$HTTP_REDIS_DATABASE_HOST_USER \
  -e REDIS_PASSWORD=$HTTP_REDIS_DATABASE_PASSWORD \
  -p $HTTP_REDIS_DATABASE_HOST_PORT:6379 \
  -d redis \
  sh -c 'echo "databases 16" > /tmp/redis.conf && \
         echo "user $REDIS_USER on >$REDIS_PASSWORD ~* +@all" >> /tmp/redis.conf && \
         redis-server /tmp/redis.conf'
         
#包專案image
docker build --no-cache -t itri-intent-backend ./Backend

# 啟動 意圖後端系統 容器
docker run -d --network $NETWORK \
    --name itri-intent-backend \
    -p $HTTP_WORKFLOW_MGT_PORT:$HTTP_WORKFLOW_MGT_PORT \
    itri-intent-backend