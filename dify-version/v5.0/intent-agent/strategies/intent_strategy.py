"""
ITRI O-RAN Intent Agent Strategy（v5.0 重構版）

職責：
1. 組裝 system prompt（單一 prompts/prompt.md）
2. 接收多模態輸入（image_files + audio_files）
3. 驅動 agent loop（LLM function calling）
4. 透過 tool_registry 執行工具（含 3 次重試）
5. Strategy 層判斷 step-by-step 模式（使用 <pending_state> 標籤跨輪次持久化）
6. 偵測 mode 切換關鍵字（auto / step-by-step）
7. 結構化日誌輸出（給日誌監控前端使用）
"""
import base64
import json
import logging
import re
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import requests as http_requests
from pydantic import BaseModel

from dify_plugin.entities.agent import AgentInvokeMessage
from dify_plugin.entities.model.llm import LLMModelConfig
from dify_plugin.entities.model.message import (
    AssistantPromptMessage,
    PromptMessageTool,
    SystemPromptMessage,
    ToolPromptMessage,
    UserPromptMessage,
)
from dify_plugin.entities.tool import ToolInvokeMessage
from dify_plugin.interfaces.agent import AgentStrategy

try:
    from dify_plugin.entities.model.message import (
        AudioPromptMessageContent,
        ImagePromptMessageContent,
        TextPromptMessageContent,
    )
    HAS_MULTIMODAL = True
except ImportError:
    HAS_MULTIMODAL = False

from strategies.tool_registry import (
    QUERY_TOOLS,
    RISKY_TOOLS,
    TOOL_SCHEMAS,
    build_payload,
    call_n8n_with_retry,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# 結構化日誌輸出（供 monitor 容器讀取）
# ─────────────────────────────────────────
_STRUCTURED_LOG_PATH = "/app/storage/intent_strategy_events.jsonl"


def _emit_event(event_type: str, **fields: Any) -> None:
    """寫入結構化事件日誌（JSON Lines）。"""
    record = {
        "ts": time.time(),
        "event": event_type,
        **fields,
    }
    try:
        with open(_STRUCTURED_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass
    logger.info("[event] %s %s", event_type, json.dumps(fields, ensure_ascii=False, default=str)[:300])


# ─────────────────────────────────────────
# 預設常數
# ─────────────────────────────────────────
N8N_BASE_URL_DEFAULT = "http://172.27.94.1:5678/webhook"

MODE_KEYWORDS_AUTO = ("自動執行", "不用確認", "直接做", "直接執行")
MODE_KEYWORDS_STEP = ("逐步確認", "我要確認每一步", "先讓我看", "一步一步")

PENDING_STATE_PATTERN = re.compile(
    r"<pending_state>\s*(.*?)\s*</pending_state>", re.DOTALL
)


# ─────────────────────────────────────────
# 參數
# ─────────────────────────────────────────
class Params(BaseModel):
    model_config = {"extra": "allow"}
    model: Any
    query: str
    maximum_iterations: int = 5
    n8n_base_url: str = N8N_BASE_URL_DEFAULT
    execution_mode: str = "auto"
    image_files: Any = None
    audio_files: Any = None


# ─────────────────────────────────────────
# Strategy 主體
# ─────────────────────────────────────────
class IntentStrategy(AgentStrategy):

    # ─────────────────────────────────────
    # Prompt 載入
    # ─────────────────────────────────────
    def _load_prompt(self) -> str:
        prompt_path = Path(__file__).parent.parent / "prompts" / "prompt.md"
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8")
        return "You are a helpful assistant."

    # ─────────────────────────────────────
    # Mode 偵測
    # ─────────────────────────────────────
    @staticmethod
    def _detect_mode_override(query: str, current_mode: str) -> str:
        """偵測 query 中的 mode 切換關鍵字，回傳實際應使用的 mode。"""
        if any(kw in query for kw in MODE_KEYWORDS_AUTO):
            return "auto"
        if any(kw in query for kw in MODE_KEYWORDS_STEP):
            return "step-by-step"
        return current_mode

    # ─────────────────────────────────────
    # Pending state 偵測（從 history 解析）
    # ─────────────────────────────────────
    @staticmethod
    def _detect_pending_state(history_messages: list[Any]) -> dict | None:
        """
        從歷史訊息中找最近一則 assistant 訊息，檢查是否含 <pending_state> 標籤。
        若有，回傳解析後的結構化資料；否則回傳 None。
        """
        for msg in reversed(history_messages):
            content = ""
            if isinstance(msg, dict):
                if msg.get("role") != "assistant":
                    continue
                raw = msg.get("content", "")
                if isinstance(raw, list):
                    content = "".join(
                        item.get("text", "") if isinstance(item, dict) else str(item)
                        for item in raw
                    )
                else:
                    content = str(raw)
            else:
                if not isinstance(msg, AssistantPromptMessage):
                    continue
                if isinstance(msg.content, str):
                    content = msg.content
                elif isinstance(msg.content, list):
                    content = "".join(getattr(item, "data", "") for item in msg.content)

            if not content:
                continue

            match = PENDING_STATE_PATTERN.search(content)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    return None
            return None
        return None

    # ─────────────────────────────────────
    # 檔案處理
    # ─────────────────────────────────────
    @staticmethod
    def _file_to_content(file_obj: Any, kind: str) -> Any | None:
        """
        將 Dify File 物件轉為 PromptMessageContent。kind: 'image' | 'audio'

        策略：
        1. 優先使用 URL 模式（讓 Dify model provider 負責下載與編碼）
        2. URL 不可用時，fallback 為 base64 模式（plugin daemon 自行處理）
        """
        if not HAS_MULTIMODAL:
            return None
        try:
            mime_type = getattr(file_obj, "mime_type", None) or (
                "image/png" if kind == "image" else "audio/mpeg"
            )
            extension = (getattr(file_obj, "extension", None) or "").lstrip(".")
            fmt = extension or ("png" if kind == "image" else "mp3")
            url = getattr(file_obj, "url", None)

            # 優先使用 URL 模式
            if url:
                if kind == "image":
                    return ImagePromptMessageContent(
                        url=url,
                        format=fmt,
                        mime_type=mime_type,
                        detail=ImagePromptMessageContent.DETAIL.HIGH,
                    )
                return AudioPromptMessageContent(
                    url=url,
                    format=fmt,
                    mime_type=mime_type,
                )

            # Fallback：base64 模式
            blob = getattr(file_obj, "blob", None)
            if blob is None:
                return None
            b64 = base64.b64encode(blob).decode("utf-8")
            if kind == "image":
                return ImagePromptMessageContent(
                    base64_data=b64,
                    format=fmt,
                    mime_type=mime_type,
                    detail=ImagePromptMessageContent.DETAIL.HIGH,
                )
            return AudioPromptMessageContent(
                base64_data=b64,
                format=fmt,
                mime_type=mime_type,
            )
        except Exception as e:
            logger.error("[file_to_content] %s file failed: %s", kind, e)
            return None

    @staticmethod
    def _normalize_file_param(value: Any) -> list[Any]:
        """
        將 Dify 傳入的 files 參數正規化成 list。
        過濾掉無效條目（None、空 dict、沒有 url 且沒有 blob 的假檔案）。
        """
        if value is None:
            return []
        items = value if isinstance(value, list) else [value]

        result = []
        for item in items:
            if item is None:
                continue
            # 同時支援 File 物件和 dict
            if isinstance(item, dict):
                if not item:
                    continue
                has_url = bool(item.get("url"))
                has_blob = bool(item.get("blob") or item.get("base64_data"))
                if not (has_url or has_blob):
                    continue
            else:
                has_url = bool(getattr(item, "url", None))
                has_blob = getattr(item, "blob", None) is not None
                if not (has_url or has_blob):
                    continue
            result.append(item)
        return result

    # ─────────────────────────────────────
    # 歷史訊息解析
    # ─────────────────────────────────────
    @staticmethod
    def _parse_history(model_data: dict) -> list[Any]:
        history: list[Any] = []
        if not isinstance(model_data, dict):
            return history
        for msg in model_data.get("history_prompt_messages", []) or []:
            if not isinstance(msg, dict):
                history.append(msg)
                continue
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                history.append(UserPromptMessage(content=content))
            elif role == "assistant":
                history.append(AssistantPromptMessage(content=content))
            elif role == "system":
                history.append(SystemPromptMessage(content=content))
        return history

    # ─────────────────────────────────────
    # 工具呼叫包裝
    # ─────────────────────────────────────
    def _execute_tool(
        self,
        tool_name: str,
        args: dict,
        n8n_base_url: str,
    ) -> tuple[str, bytes | None, dict]:
        payload = build_payload(tool_name, args)
        text, raw_bytes, meta = call_n8n_with_retry(tool_name, payload, n8n_base_url)
        _emit_event(
            "tool_executed",
            tool=tool_name,
            args=args,
            attempts=meta.get("attempts"),
            elapsed_ms=meta.get("elapsed_ms"),
            status_code=meta.get("status_code"),
            error=meta.get("error"),
        )
        return text, raw_bytes, meta

    def _upload_image(self, raw_bytes: bytes, name: str) -> str | None:
        """將圖片上傳至 Dify file storage，回傳 preview URL。"""
        try:
            import os as _os
            from dify_plugin.core.entities.invocation import InvokeType

            api_base = (
                _os.environ.get("DIFY_INNER_API_URL", "").rstrip("/")
                or _os.environ.get("PLUGIN_DIFY_INNER_API_URL", "").rstrip("/")
                or "http://api:5001"
            )
            signed_url = None
            for resp_chunk in self.session.file._backwards_invoke(
                InvokeType.UploadFile, dict,
                {"filename": f"{name}.png", "mimetype": "image/png"},
            ):
                signed_url = resp_chunk.get("url", "")
                break

            if not signed_url:
                return None
            if signed_url.startswith("/"):
                signed_url = f"{api_base}{signed_url}"

            upload_resp = http_requests.post(
                signed_url,
                files={"file": (f"{name}.png", raw_bytes, "image/png")},
                timeout=30,
            )
            if upload_resp.status_code != 201:
                return None
            file_data = upload_resp.json()
            preview_url = file_data.get("preview_url", "")
            if preview_url.startswith("/"):
                preview_url = f"{api_base}{preview_url}"
            return preview_url or None
        except Exception as e:
            logger.error("[upload_image] failed: %s", e)
            return None

    # ─────────────────────────────────────
    # 主入口
    # ─────────────────────────────────────
    def _invoke(self, parameters: dict[str, Any]) -> Generator[AgentInvokeMessage, None, None]:
        parameters.pop("tools", None)
        try:
            yield from self._do_invoke(parameters)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error("[IntentStrategy] FATAL: %s\n%s", e, tb)
            _emit_event("fatal_error", error=str(e), traceback=tb)
            yield self.create_text_message(text=f"Agent 執行錯誤：{e}")

    def _do_invoke(self, parameters: dict[str, Any]) -> Generator[AgentInvokeMessage, None, None]:
        # 診斷：記錄 Dify 實際傳入的 files 參數原貌
        raw_image = parameters.get("image_files")
        raw_audio = parameters.get("audio_files")
        _emit_event(
            "params_debug",
            param_keys=list(parameters.keys()),
            image_files_type=type(raw_image).__name__,
            image_files_repr=str(raw_image)[:500],
            audio_files_type=type(raw_audio).__name__,
            audio_files_repr=str(raw_audio)[:500],
        )

        params = Params(**parameters)
        n8n_base_url = params.n8n_base_url or N8N_BASE_URL_DEFAULT

        _emit_event(
            "request_start",
            query_preview=params.query[:200],
            execution_mode=params.execution_mode,
        )

        # ── 解析模型參數與歷史 ──
        model_data = (
            params.model
            if isinstance(params.model, dict)
            else params.model.model_dump(mode="json")
            if hasattr(params.model, "model_dump")
            else {}
        )
        history_messages = self._parse_history(model_data)

        # ── 偵測 mode 切換關鍵字 ──
        effective_mode = self._detect_mode_override(params.query, params.execution_mode)
        if effective_mode != params.execution_mode:
            _emit_event(
                "mode_switched",
                from_mode=params.execution_mode,
                to_mode=effective_mode,
                trigger="keyword_in_query",
            )

        # ── 偵測 history 中的 pending state ──
        pending_state = self._detect_pending_state(history_messages)
        pending_was_seen = pending_state is not None
        if pending_was_seen:
            _emit_event("pending_state_detected", pending=pending_state)

        # ── 組裝 system prompt（單一 prompt.md）──
        system_prompt = self._load_prompt()

        # ── 組裝 user message（含圖片 + 音訊）──
        user_message = self._build_user_message(params)

        messages: list[Any] = [
            SystemPromptMessage(content=system_prompt),
            *history_messages,
            user_message,
        ]

        # ── 工具定義 ──
        prompt_tools = [
            PromptMessageTool(
                name=schema["name"],
                description=schema["description"],
                parameters=schema["parameters"],
            )
            for schema in TOOL_SCHEMAS
        ]

        log_main = self.create_log_message(
            label="[Strategy] 處理用戶查詢",
            data={
                "query_preview": params.query[:200],
                "history_count": len(history_messages),
                "execution_mode": effective_mode,
                "pending_was_seen": pending_was_seen,
                "image_files": len(self._normalize_file_param(params.image_files)),
                "audio_files": len(self._normalize_file_param(params.audio_files)),
            },
            status=ToolInvokeMessage.LogMessage.LogStatus.START,
        )
        yield log_main

        response_text = ""
        iteration = 0

        for iteration in range(params.maximum_iterations):
            _emit_event(
                "llm_invoke_start",
                iteration=iteration + 1,
                **self._inspect_messages(messages),
            )

            log_iter = self.create_log_message(
                label=f"[LLM] 迭代 {iteration + 1}",
                data={"iteration": iteration + 1, "messages_count": len(messages)},
                status=ToolInvokeMessage.LogMessage.LogStatus.START,
            )
            yield log_iter

            try:
                model_config_data = (
                    params.model
                    if isinstance(params.model, dict)
                    else params.model.model_dump(mode="json")
                )
                t0 = time.time()
                chunks = self.session.model.llm.invoke(
                    model_config=LLMModelConfig(**model_config_data),
                    prompt_messages=messages,
                    tools=prompt_tools if prompt_tools else None,
                    stream=True,
                )
            except Exception as e:
                _emit_event("llm_invoke_error", iteration=iteration + 1, error=str(e))
                yield self.create_text_message(text=f"LLM 呼叫失敗：{e}")
                return

            response_text = ""
            tool_calls: list[tuple[str, str, dict]] = []

            for chunk in chunks:
                if chunk.delta.message and chunk.delta.message.content:
                    c = chunk.delta.message.content
                    if isinstance(c, list):
                        for item in c:
                            response_text += getattr(item, "data", "")
                    else:
                        response_text += str(c)
                if (
                    chunk.delta.message
                    and hasattr(chunk.delta.message, "tool_calls")
                    and chunk.delta.message.tool_calls
                ):
                    for tc in chunk.delta.message.tool_calls:
                        tool_calls.append((
                            tc.id,
                            tc.function.name,
                            json.loads(tc.function.arguments) if tc.function.arguments else {},
                        ))

            elapsed_ms = int((time.time() - t0) * 1000)
            _emit_event(
                "llm_invoke_end",
                iteration=iteration + 1,
                elapsed_ms=elapsed_ms,
                response_chars=len(response_text),
                response_preview=response_text[:120],
                tool_calls=[name for _, name, _ in tool_calls],
            )

            # ── 沒有 tool_calls：結束 ──
            if not tool_calls:
                yield self.finish_log_message(log=log_iter, data={"reply_preview": response_text[:300]})
                break

            # ── 有 tool_calls：判斷是否需攔截確認 ──
            risky_calls = [
                (tc_id, name, args) for tc_id, name, args in tool_calls if name in RISKY_TOOLS
            ]

            should_intercept = (
                effective_mode == "step-by-step"
                and bool(risky_calls)
                and not pending_was_seen
            )

            if should_intercept:
                # 第一次：產生 <pending_state> 並回覆，不執行
                pending_payload = {
                    "tool_calls": [
                        {"id": tc_id, "name": name, "args": args}
                        for tc_id, name, args in tool_calls
                    ],
                    "created_at": time.time(),
                }
                pending_text = (
                    response_text
                    + f"\n\n<pending_state>\n{json.dumps(pending_payload, ensure_ascii=False)}\n</pending_state>"
                )
                _emit_event(
                    "tool_intercepted",
                    iteration=iteration + 1,
                    risky_tools=[name for _, name, _ in risky_calls],
                )
                yield self.finish_log_message(
                    log=log_iter,
                    data={"action": "intercepted", "tools": [name for _, name, _ in risky_calls]},
                )
                yield self.finish_log_message(
                    log=log_main,
                    data={"status": "awaiting_confirmation"},
                )
                yield self.create_text_message(text=pending_text)
                _emit_event("request_end", status="awaiting_confirmation")
                return

            # ── 執行工具 ──
            messages.append(
                AssistantPromptMessage(
                    content=response_text,
                    tool_calls=[
                        {
                            "id": tc_id,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                        for tc_id, name, args in tool_calls
                    ],
                )
            )

            yield self.finish_log_message(
                log=log_iter,
                data={"action": "execute", "tools": [name for _, name, _ in tool_calls]},
            )

            for tc_id, name, args in tool_calls:
                # Strategy 主動插入 [呼叫工具] 標籤給前端顯示
                args_str = json.dumps(args, ensure_ascii=False)
                tool_call_marker = f"\n[呼叫工具]\n工具：{name}\n參數：{args_str}\n"
                yield self.create_text_message(text=tool_call_marker)

                log_tool = self.create_log_message(
                    label=f"[Tool] {name}",
                    data={"tool": name, "args": args},
                    status=ToolInvokeMessage.LogMessage.LogStatus.START,
                )
                yield log_tool

                result, raw_bytes, meta = self._execute_tool(name, args, n8n_base_url)

                if raw_bytes is not None:
                    preview_url = self._upload_image(raw_bytes, name)
                    if preview_url:
                        yield self.create_image_message(image_url=preview_url)

                yield self.finish_log_message(
                    log=log_tool,
                    data={
                        "result_preview": result[:500],
                        "attempts": meta.get("attempts"),
                        "elapsed_ms": meta.get("elapsed_ms"),
                    },
                )
                messages.append(
                    ToolPromptMessage(content=result, tool_call_id=tc_id, name=name)
                )

        yield self.finish_log_message(
            log=log_main,
            data={
                "total_iterations": iteration + 1,
                "response_chars": len(response_text),
                "execution_mode": effective_mode,
                "status": "completed",
            },
        )
        yield self.create_text_message(text=response_text)
        _emit_event("request_end", status="completed", iterations=iteration + 1)

    # ─────────────────────────────────────
    # ─────────────────────────────────────
    # LLM 輸入訊息結構診斷
    # ─────────────────────────────────────
    @staticmethod
    def _inspect_messages(messages: list[Any]) -> dict[str, Any]:
        """
        檢查送入 LLM 的 messages 結構，回傳診斷資訊：
        - messages_count：總訊息數
        - user_content_type：user message 的 content 類型（str / list）
        - user_parts：若為 list，各 part 的類型名稱列表
        - image_parts / audio_parts：多模態 part 數量
        """
        result: dict[str, Any] = {"messages_count": len(messages)}
        for msg in messages:
            if not isinstance(msg, UserPromptMessage):
                continue
            content = msg.content
            if isinstance(content, str):
                result["user_content_type"] = "str"
                result["image_parts"] = 0
                result["audio_parts"] = 0
            elif isinstance(content, list):
                type_names = [type(p).__name__ for p in content]
                image_parts = sum(1 for n in type_names if "Image" in n)
                audio_parts = sum(1 for n in type_names if "Audio" in n)
                text_parts  = sum(1 for n in type_names if "Text" in n)
                result["user_content_type"] = "multimodal"
                result["text_parts"]  = text_parts
                result["image_parts"] = image_parts
                result["audio_parts"] = audio_parts
                result["has_image"] = image_parts > 0
                result["has_audio"] = audio_parts > 0
            break
        return result

    # ─────────────────────────────────────
    # 從 query 文字中解析並下載嵌入的圖片 URL
    # 格式：[IMAGE_URL_N]http://...[/IMAGE_URL_N]
    # ─────────────────────────────────────
    _IMAGE_URL_TAG_RE = re.compile(r"\[IMAGE_URL_\d+\](.*?)\[/IMAGE_URL_\d+\]")

    @classmethod
    def _extract_image_urls(cls, query: str) -> tuple[str, list[str]]:
        """
        從 query 中提取所有 [IMAGE_URL_N]...[/IMAGE_URL_N] 標籤，
        回傳 (乾淨的 query 文字, [url, ...])。
        """
        urls = cls._IMAGE_URL_TAG_RE.findall(query)
        clean_query = cls._IMAGE_URL_TAG_RE.sub("", query).strip()
        return clean_query, urls

    @staticmethod
    def _url_to_image_content(url: str) -> "tuple[ImagePromptMessageContent | None, int, str | None]":
        """
        從 URL 下載圖片並轉成 base64 ImagePromptMessageContent。
        回傳 (content_or_None, bytes_size, error_or_None)。
        """
        if not HAS_MULTIMODAL:
            return None, 0, "HAS_MULTIMODAL=False"
        t0 = time.time()
        try:
            resp = http_requests.get(url, timeout=15)
            resp.raise_for_status()
            raw = resp.content
            mime = resp.headers.get("Content-Type", "image/png").split(";")[0].strip()
            fmt = mime.split("/")[-1] if "/" in mime else "png"
            b64 = base64.b64encode(raw).decode("utf-8")
            elapsed_ms = int((time.time() - t0) * 1000)
            content = ImagePromptMessageContent(
                base64_data=b64,
                format=fmt,
                mime_type=mime,
                detail=ImagePromptMessageContent.DETAIL.HIGH,
            )
            return content, len(raw), None
        except Exception as e:
            logger.warning("[image_url] Failed to download %s: %s", url, e)
            return None, 0, str(e)

    # User message 組裝（含多模態檔案）
    # ─────────────────────────────────────
    def _build_user_message(self, params: Params) -> UserPromptMessage:
        image_files = self._normalize_file_param(params.image_files)
        audio_files = self._normalize_file_param(params.audio_files)

        # 從 query 文字中解析嵌入的 [IMAGE_URL_N] 標籤（Backend 嵌入路徑）
        clean_query, embedded_urls = self._extract_image_urls(params.query)

        has_any = image_files or audio_files or embedded_urls
        if not has_any or not HAS_MULTIMODAL:
            return UserPromptMessage(content=params.query)

        parts: list[Any] = [TextPromptMessageContent(data=clean_query)]

        # 優先處理 Dify File 物件（image_files 參數）
        for idx, f in enumerate(image_files):
            content = self._file_to_content(f, "image")
            if content is not None:
                parts.append(content)
                _emit_event("multimodal_file_added", kind="image", source="dify_file", index=idx)

        # 處理 query 中嵌入的圖片 URL（下載並轉 base64）
        for idx, url in enumerate(embedded_urls):
            _emit_event("image_download_start", index=idx, url=url)
            t0 = time.time()
            content, size_bytes, error = self._url_to_image_content(url)
            elapsed_ms = int((time.time() - t0) * 1000)
            if content is not None:
                parts.append(content)
                _emit_event(
                    "image_download_done",
                    index=idx,
                    url=url,
                    size_kb=round(size_bytes / 1024, 1),
                    elapsed_ms=elapsed_ms,
                )
            else:
                _emit_event(
                    "image_download_failed",
                    index=idx,
                    url=url,
                    error=error,
                    elapsed_ms=elapsed_ms,
                )

        # 處理音訊檔案
        for idx, f in enumerate(audio_files):
            content = self._file_to_content(f, "audio")
            if content is not None:
                parts.append(content)
                _emit_event("multimodal_file_added", kind="audio", source="dify_file", index=idx)

        _emit_event(
            "user_message_built",
            text_chars=len(clean_query),
            image_file_count=len(image_files),
            embedded_url_count=len(embedded_urls),
            audio_count=len(audio_files),
            total_parts=len(parts),
        )
        return UserPromptMessage(content=parts)
