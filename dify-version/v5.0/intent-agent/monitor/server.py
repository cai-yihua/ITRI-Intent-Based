"""
Intent Agent 即時日誌監控服務（開發用）

雙來源日誌：
1. Strategy 內部結構化 log（JSON Lines）
   - 路徑：/data/intent_strategy_events.jsonl（從 Plugin daemon 容器掛載）
   - 由 strategies/intent_strategy.py 中 _emit_event() 寫入
2. Plugin daemon container stdout/stderr
   - 透過環境變數 PLUGIN_CONTAINER_NAME 指定 container name
   - 使用 docker logs --follow 讀取（需掛載 docker socket）

對外提供：
- GET /                : 靜態 HTML 頁面
- GET /api/events/sse  : SSE 串流，即時推送事件
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse

STRUCTURED_LOG_PATH = Path(os.environ.get("STRUCTURED_LOG_PATH", "/data/intent_strategy_events.jsonl"))
PLUGIN_CONTAINER_NAME = os.environ.get("PLUGIN_CONTAINER_NAME", "")
HTML_PATH = Path(__file__).parent / "index.html"

app = FastAPI(title="Intent Agent Log Monitor")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(HTML_PATH, media_type="text/html")


async def _tail_jsonl(path: Path) -> AsyncIterator[dict]:
    """Tail JSONL 檔案，每行一個事件。檔案不存在時等待。"""
    while not path.exists():
        await asyncio.sleep(1)
    with path.open("r", encoding="utf-8") as f:
        f.seek(0, 2)  # 跳到檔尾
        while True:
            line = f.readline()
            if not line:
                await asyncio.sleep(0.5)
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


async def _tail_container_logs(container_name: str) -> AsyncIterator[dict]:
    """Tail Docker container 的 stdout/stderr，解析 [event] 標記行為結構化事件。"""
    if not container_name:
        return
    proc = await asyncio.create_subprocess_exec(
        "docker", "logs", "--follow", "--tail", "0", container_name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    if proc.stdout is None:
        return
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="replace").rstrip("\n")
        if not text:
            continue

        # 解析 Strategy 寫入的結構化事件標記
        # 格式：... [event] <event_type> <json_fields_truncated>
        idx = text.find("[event] ")
        if idx != -1:
            rest = text[idx + 8:].strip()
            parts = rest.split(" ", 1)
            event_type = parts[0]
            fields: dict = {}
            if len(parts) > 1:
                try:
                    fields = json.loads(parts[1])
                except json.JSONDecodeError:
                    # JSON 可能被截斷（logger 限制 300 字元），保留原始字串
                    fields = {"_raw": parts[1]}
            yield {"ts": time.time(), "event": event_type, **fields}
            continue

        # 一般 container log 行
        yield {
            "ts": time.time(),
            "event": "container_log",
            "source": container_name,
            "line": text,
        }


async def _merge_streams(*streams: AsyncIterator[dict]) -> AsyncIterator[dict]:
    """合併多個 async iterator，先到先送。"""
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def pump(stream: AsyncIterator[dict]) -> None:
        try:
            async for item in stream:
                await queue.put(item)
        finally:
            await queue.put(None)

    tasks = [asyncio.create_task(pump(s)) for s in streams]
    finished = 0
    try:
        while finished < len(tasks):
            item = await queue.get()
            if item is None:
                finished += 1
                continue
            yield item
    finally:
        for t in tasks:
            t.cancel()


@app.get("/api/events/sse")
async def stream_events() -> StreamingResponse:
    """SSE 端點，將事件以 text/event-stream 推送給前端。"""

    async def event_generator() -> AsyncIterator[bytes]:
        # 開頭傳一個 hello 事件
        hello = {"ts": time.time(), "event": "monitor_ready"}
        yield f"data: {json.dumps(hello, ensure_ascii=False)}\n\n".encode("utf-8")

        streams: list[AsyncIterator[dict]] = [_tail_jsonl(STRUCTURED_LOG_PATH)]
        if PLUGIN_CONTAINER_NAME:
            streams.append(_tail_container_logs(PLUGIN_CONTAINER_NAME))

        async for record in _merge_streams(*streams):
            payload = json.dumps(record, ensure_ascii=False, default=str)
            yield f"data: {payload}\n\n".encode("utf-8")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "structured_log_exists": STRUCTURED_LOG_PATH.exists(),
        "plugin_container": PLUGIN_CONTAINER_NAME or None,
    }
