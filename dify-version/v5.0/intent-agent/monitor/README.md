# Intent Agent Log Monitor

開發用的即時日誌監控服務，獨立於主 plugin 容器運行。

## 功能

- 即時 SSE 推送 Strategy 內部結構化事件（`/tmp/intent_strategy_events.jsonl`）
- （可選）即時 tail Plugin daemon container 的 stdout/stderr
- 純 HTML 前端，支援關鍵字過濾、自動滾動、清空

## 啟動

```bash
docker compose up --build -d
```

開啟瀏覽器：<http://localhost:8765>

## 環境變數

| 變數 | 說明 | 預設 |
|------|------|------|
| `STRUCTURED_LOG_PATH` | Strategy 結構化 log 檔案路徑（容器內） | `/data/intent_strategy_events.jsonl` |
| `PLUGIN_CONTAINER_NAME` | 想監控的 plugin daemon container 名稱 | （空，停用） |

## Volume 設定

Strategy 寫入的 log 在 plugin daemon 容器內位於 `/tmp/intent_strategy_events.jsonl`，需要將此路徑掛載到 host，monitor 容器才能讀取：

1. 在 host 建立資料夾：`mkdir -p /tmp/intent-agent-events`
2. 確認 plugin daemon 容器把 `/tmp` 掛到 host 同一路徑（或在 plugin daemon 的 docker-compose 中加 volume）
3. monitor 容器把 host 的 `/tmp/intent-agent-events` 掛到 `/data`

如需追加 container logs 來源，需開放 docker socket 並設定 `PLUGIN_CONTAINER_NAME`。

## API

- `GET /` — 監控頁面
- `GET /api/events/sse` — SSE 事件串流
- `GET /api/health` — 健康檢查
