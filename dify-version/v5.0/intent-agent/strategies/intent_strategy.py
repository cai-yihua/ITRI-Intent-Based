import json
import logging
import re
from pathlib import Path
from collections.abc import Generator
from typing import Any

import requests as http_requests

logger = logging.getLogger(__name__)

_DEBUG_LOG_PATH = "/tmp/intent_strategy_debug.log"
def _debug_log(msg: str):
    """寫入 debug log 到檔案（plugin subprocess 的 print/logger 不一定會顯示）"""
    try:
        with open(_DEBUG_LOG_PATH, "a") as f:
            f.write(f"{msg}\n")
    except Exception:
        pass
from pydantic import BaseModel
from dify_plugin.entities.agent import AgentInvokeMessage
from dify_plugin.entities.model.llm import LLMModelConfig
from dify_plugin.entities.model.message import (
    SystemPromptMessage,
    UserPromptMessage,
    AssistantPromptMessage,
    ToolPromptMessage,
    PromptMessageTool,
)
from dify_plugin.entities.tool import ToolInvokeMessage
from dify_plugin.interfaces.agent import AgentStrategy, ToolEntity

# n8n base URL 預設值
N8N_BASE_URL_DEFAULT = "http://172.27.94.1:5678/webhook"


class Params(BaseModel):
    model_config = {"extra": "allow"}  # 允許額外欄位（如 tools=None）被忽略
    model: Any
    query: str
    maximum_iterations: int = 5
    n8n_base_url: str = N8N_BASE_URL_DEFAULT
    execution_mode: str = "auto"


# ─────────────────────────────────────────
# 執行模式動態 Prompt 片段
# ─────────────────────────────────────────
MODE_PROMPT_AUTO = """
## 當前執行模式：自動執行（auto）
- 當意圖清晰且參數完整時，直接呼叫工具執行，不需額外向使用者確認。
- 執行後仍須完整呈現執行過程與結果。
- 若使用者在對話中說「逐步確認」或「我要確認每一步」，則切換為 step-by-step 行為。
"""

MODE_PROMPT_STEP_BY_STEP = """
## 當前執行模式：逐步確認（step-by-step）
- 所有高風險操作（enable_im, simulate_im, enable_qoe, simulate_qoe, disable_im, disable_qoe）執行前，必須先描述執行計畫並等使用者確認（是/否）。
- 查詢類操作（get_ue_status, get_sinr_map, get_active_rapp_status）可直接執行。
- 若使用者在對話中說「自動執行」或「不用確認」，則切換為 auto 行為。
"""


# ─────────────────────────────────────────
# 內建工具定義（不依賴 Dify Tool System）
# ─────────────────────────────────────────
BUILTIN_TOOLS = [
    {
        "name": "get_ue_status",
        "description": "查詢 UE 狀態。參數互斥，依優先級擇一使用：1. ueid（指定 UE ID，純數字字串）2. location（地點代碼：電梯前=131, 501走廊=132, 503會議室=135）3. all_edge=true（所有受干擾 UE）4. all_center=true（所有未受干擾 UE）5. worst_part（效能最差比例，0.1=最差10%）6. 全部不填=查詢所有 UE",
        "parameters": {
            "type": "object",
            "properties": {
                "ueid": {"type": "string", "description": "UE ID（純數字）"},
                "location": {"type": "string", "description": "地點代碼：131=電梯前, 132=501走廊, 135=503會議室"},
                "all_edge": {"type": "boolean", "description": "true=查詢所有受干擾 UE"},
                "all_center": {"type": "boolean", "description": "true=查詢所有未受干擾 UE"},
                "worst_part": {"type": "number", "description": "效能最差比例（0.1=最差10%）"},
            },
            "required": [],
        },
        "endpoint": "e8f1cc4d-7560-4ae6-8ec2-dece817160be",
        "method": "POST",
    },
    {
        "name": "get_sinr_map",
        "description": "查詢場域的 SINR 熱力圖，無需任何參數。",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "endpoint": "9a74bbcd-861d-4c03-b3b4-1410c6dddb08",
        "method": "POST",
    },
    {
        "name": "get_active_rapp_status",
        "description": "查詢目前場域中正在執行的優化狀態（rApp）。用於確認是否有優化正在運行。執行 enable_im/simulate_im/enable_qoe/simulate_qoe 前必須先調用此工具檢查。無需任何參數。",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "endpoint": "e36fcc1c-bf54-4590-a894-c00bcb5c318c",
        "method": "POST",
    },
    {
        "name": "enable_im",
        "description": "開啟干擾管理(IM)優化。執行前必須先調用 get_active_rapp_status 確認無優化正在執行。參數互斥，依優先級擇一使用：1. location（地點代碼：電梯前=131, 501走廊=132, 503會議室=135）2. all_edge=true（所有受干擾 UE）3. all_center=true（所有未受干擾 UE）4. worst_part（效能最差比例，0.1=最差10%）5. 全部不填=優化整個場域。可選 optimization_inc_percent（提升比例，預設0.1即10%）。",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "地點代碼：131=電梯前, 132=501走廊, 135=503會議室"},
                "all_edge": {"type": "boolean", "description": "true=所有受干擾 UE"},
                "all_center": {"type": "boolean", "description": "true=所有未受干擾 UE"},
                "worst_part": {"type": "number", "description": "效能最差比例（0.1=最差10%）"},
                "optimization_inc_percent": {"type": "number", "description": "提升比例，預設0.1即10%"},
            },
            "required": [],
        },
        "endpoint": "7aa374ea-d239-462c-987e-42872a27133e",
        "method": "POST",
        "build_payload": "im",
    },
    {
        "name": "disable_im",
        "description": "關閉干擾管理(IM)優化。無需任何參數。",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "endpoint": "0ea63998-8332-4626-9452-34c923fa538e",
        "method": "GET",
    },
    {
        "name": "simulate_im",
        "description": "模擬干擾管理(IM)優化效果，不會實際啟用。執行前必須先調用 get_active_rapp_status 確認無優化正在執行。參數同 enable_im。",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "地點代碼：131=電梯前, 132=501走廊, 135=503會議室"},
                "all_edge": {"type": "boolean", "description": "true=所有受干擾 UE"},
                "all_center": {"type": "boolean", "description": "true=所有未受干擾 UE"},
                "worst_part": {"type": "number", "description": "效能最差比例（0.1=最差10%）"},
                "optimization_inc_percent": {"type": "number", "description": "提升比例，預設0.1即10%"},
            },
            "required": [],
        },
        "endpoint": "da0e5281-0fbc-4c30-adb6-76b23824ec28",
        "method": "POST",
        "build_payload": "im",
    },
    {
        "name": "enable_qoe",
        "description": "針對特定 UE 啟用 QoE 優化。執行前必須先調用 get_active_rapp_status 確認無優化正在執行。必須提供 ueid 參數。",
        "parameters": {
            "type": "object",
            "properties": {
                "ueid": {"type": "string", "description": "目標 UE ID（純數字字串）"},
                "optimization_inc_percent": {"type": "number", "description": "提升比例，預設0.1即10%"},
            },
            "required": ["ueid"],
        },
        "endpoint": "fbc98177-6e62-4738-9e47-2312896ff5da",
        "method": "POST",
        "build_payload": "qoe",
    },
    {
        "name": "disable_qoe",
        "description": "關閉特定 UE 的 QoE 優化。必須提供 ueid 參數。",
        "parameters": {
            "type": "object",
            "properties": {
                "ueid": {"type": "string", "description": "目標 UE ID（純數字字串）"},
            },
            "required": ["ueid"],
        },
        "endpoint": "009cca49-effc-492a-b985-e14e0a95b133",
        "method": "POST",
        "build_payload": "qoe_disable",
    },
    {
        "name": "simulate_qoe",
        "description": "模擬特定 UE 的 QoE 優化效果，不會實際啟用。執行前必須先調用 get_active_rapp_status 確認無優化正在執行。必須提供 ueid 參數。",
        "parameters": {
            "type": "object",
            "properties": {
                "ueid": {"type": "string", "description": "目標 UE ID（純數字字串）"},
                "optimization_inc_percent": {"type": "number", "description": "提升比例，預設0.1即10%"},
            },
            "required": ["ueid"],
        },
        "endpoint": "7aa374ea-d239-462c-987e-42872a27133e",
        "method": "POST",
        "build_payload": "qoe",
    },
]

# IM payload 基礎模板
IM_BASE_PAYLOAD = {
    "manualNumOfNonInterferedRb": -1,
    "percentOfEdgeUeNumForGtCaseBound": 0.5,
    "percentOfEdgeUeNumForLtCaseBound": 0.3,
    "percentOfCenterUeAvgTputForGtCase": 0.75,
    "percentOfCenterUeAvgTputForEqCase": 0.75,
    "percentOfCenterUeAvgTputForLtCase": 0.75,
}

LOCATION_MAP = {
    "131": {"filter": {"location": "131"}, "optimization_method": 3},
    "132": {"filter": {"location": "132"}, "optimization_method": 3},
    "135": {"filter": {"location": "135"}, "optimization_method": 3},
}


def _build_payload(tool_def: dict, args: dict) -> dict | None:
    """根據工具定義組裝 API payload。"""
    build_type = tool_def.get("build_payload")
    if not build_type:
        # 直接用 args 作為 payload（get_ue_status 等）
        return args if args else None

    if build_type == "im":
        payload = dict(IM_BASE_PAYLOAD)
        payload["optimization_inc_percent"] = str(args.get("optimization_inc_percent", 0.1))
        loc = args.get("location")
        if loc and loc in LOCATION_MAP:
            m = LOCATION_MAP[loc]
            payload["filter"] = m["filter"]
            payload["optimization_method"] = m["optimization_method"]
            payload["optimization_param"] = None
        elif args.get("all_edge"):
            payload["filter"] = {"all_edge": True}
            payload["optimization_method"] = 2
            payload["optimization_param"] = None
        elif args.get("all_center"):
            payload["filter"] = {"all_center": True}
            payload["optimization_method"] = 3
            payload["optimization_param"] = None
        elif args.get("worst_part") is not None:
            wp = float(args["worst_part"])
            payload["filter"] = {"worst_part": wp}
            payload["optimization_method"] = 6
            payload["optimization_param"] = {"worst_percent": wp}
        else:
            payload["filter"] = {}
            payload["optimization_method"] = 4
            payload["optimization_param"] = None
        return payload

    if build_type == "qoe":
        return {
            "ueid": str(args.get("ueid", "")),
            "optimization_inc_percent": float(args.get("optimization_inc_percent", 0.1)),
        }

    if build_type == "qoe_disable":
        return {"ueid": str(args.get("ueid", ""))}

    return args


def _call_n8n(endpoint: str, method: str, payload: dict | None, n8n_base_url: str) -> tuple[str, bytes | None]:
    """直接 HTTP 呼叫 n8n webhook。回傳 (text_for_llm, raw_bytes_or_None)。"""
    url = f"{n8n_base_url.rstrip('/')}/{endpoint}"
    logger.info(f"[n8n] {method} {url} payload={json.dumps(payload, ensure_ascii=False)[:200] if payload else 'None'}")
    try:
        resp = http_requests.request(
            method=method,
            url=url,
            json=payload if method.upper() in ("POST", "PUT", "PATCH") else None,
            timeout=30,
        )
        content_type = resp.headers.get("Content-Type", "")
        _debug_log(f"[_call_n8n] status={resp.status_code} Content-Type={content_type} size={len(resp.content)}")

        # 圖片回傳：回傳描述文字 + raw bytes
        if content_type.startswith("image/"):
            _debug_log(f"[_call_n8n] image detected! returning raw bytes")
            return "[圖片已回傳，請描述此圖片為 SINR 熱力圖，已直接顯示給使用者]", resp.content

        return resp.text, None
    except Exception as e:
        logger.error(f"[n8n] Error calling {url}: {e}")
        return f"Error calling n8n: {e}", None


class IntentStrategy(AgentStrategy):

    def _build_system_prompt(self, execution_mode: str) -> str:
        prompt_path = Path(__file__).parent.parent / "prompts" / "prompt.md"
        base_prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else "You are a helpful assistant."
        mode_prompt = MODE_PROMPT_AUTO if execution_mode == "auto" else MODE_PROMPT_STEP_BY_STEP
        return base_prompt + "\n\n" + mode_prompt

    # ─────────────────────────────────────────
    # Trace
    # ─────────────────────────────────────────
    @staticmethod
    def _trace_entry(seq: int, from_actor: str, to_actor: str, action: str, detail: str = "") -> dict:
        entry = {"seq": seq, "from": from_actor, "to": to_actor, "action": action}
        if detail:
            entry["detail"] = detail
        return entry

    @staticmethod
    def _trace_to_text(trace: list[dict]) -> str:
        lines = ["═══ Sequence Trace ═══"]
        for t in trace:
            line = f"  [{t['seq']:>3}] {t['from']} → {t['to']:<40} : {t['action']}"
            if t.get("detail"):
                line += f"  ({t['detail']})"
            lines.append(line)
        lines.append("═══ End Trace ═══")
        return "\n".join(lines)

    # ─────────────────────────────────────────
    # Agent 主流程
    # ─────────────────────────────────────────
    def _invoke(self, parameters: dict[str, Any]) -> Generator[AgentInvokeMessage]:
        # 移除 tools 參數（工具已內建於 Strategy 中，不需要 Dify tool system）
        parameters.pop("tools", None)
        logger.info(f"[IntentStrategy] _invoke called with keys={list(parameters.keys())}")
        try:
            yield from self._do_invoke(parameters)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"[IntentStrategy] FATAL: {e}\n{tb}")
            yield self.create_text_message(text=f"Agent 執行錯誤：{e}\n\n```\n{tb}\n```")

    def _do_invoke(self, parameters: dict[str, Any]) -> Generator[AgentInvokeMessage]:
        try:
            params = Params(**parameters)
        except Exception as e:
            logger.error(f"[IntentStrategy] Params validation failed: {e}")
            raise

        trace: list[dict] = []
        seq = 0
        A_USER, A_DIFY, A_PLUGIN, A_LLM, A_N8N = "User", "Dify/AgentNode", "客服部門/客服人員", "LLM", "n8n/API"

        seq += 1; trace.append(self._trace_entry(seq, A_USER, A_DIFY, "發送查詢", params.query[:60]))
        seq += 1; trace.append(self._trace_entry(seq, A_DIFY, A_PLUGIN, "請處理用戶查詢"))

        # ── 解析歷史訊息 ──
        history_messages = []
        model_data = params.model if isinstance(params.model, dict) else params.model.model_dump(mode="json") if hasattr(params.model, "model_dump") else {}
        if isinstance(model_data, dict):
            for msg in model_data.get("history_prompt_messages", []):
                if isinstance(msg, dict):
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    if role == "user":
                        history_messages.append(UserPromptMessage(content=content))
                    elif role == "assistant":
                        history_messages.append(AssistantPromptMessage(content=content))
                else:
                    history_messages.append(msg)

        # ── n8n base URL（從參數或預設）──
        n8n_base_url = params.n8n_base_url or N8N_BASE_URL_DEFAULT

        log_main = self.create_log_message(
            label=f"[{A_DIFY} → {A_PLUGIN}] 請處理用戶查詢",
            data={"query": params.query[:200], "歷史訊息數": len(history_messages), "執行模式": params.execution_mode},
            status=ToolInvokeMessage.LogMessage.LogStatus.START,
        )
        yield log_main

        # ── System Prompt ──
        system_prompt = self._build_system_prompt(params.execution_mode)

        # ── 建立內建工具的 PromptMessageTool 列表（直接從 BUILTIN_TOOLS 定義）──
        prompt_tools = []
        tool_def_map: dict[str, dict] = {}
        for t in BUILTIN_TOOLS:
            tool_def_map[t["name"]] = t
            prompt_tools.append(PromptMessageTool(
                name=t["name"],
                description=t["description"],
                parameters=t["parameters"],
            ))

        # ── 初始化對話 ──
        messages = [
            SystemPromptMessage(content=system_prompt),
            *history_messages,
            UserPromptMessage(content=params.query),
        ]

        # ── Agent Loop ──
        response_text = ""
        iteration = 0

        for iteration in range(params.maximum_iterations):
            seq += 1
            trace.append(self._trace_entry(seq, A_PLUGIN, A_LLM, "請分析意圖並決定行動", f"{len(messages)} messages"))

            log_iter = self.create_log_message(
                label=f"[{A_PLUGIN} → {A_LLM}] 迭代 {iteration + 1}",
                data={"迭代": iteration + 1, "messages_count": len(messages), "tools": [t.name for t in prompt_tools]},
                status=ToolInvokeMessage.LogMessage.LogStatus.START,
            )
            yield log_iter

            try:
                model_config_data = params.model if isinstance(params.model, dict) else params.model.model_dump(mode="json")
                logger.info(f"[IntentStrategy] Calling LLM with model={model_config_data.get('model','?')}, tools={len(prompt_tools)}")
                chunks = self.session.model.llm.invoke(
                    model_config=LLMModelConfig(**model_config_data),
                    prompt_messages=messages,
                    tools=prompt_tools if prompt_tools else None,
                    stream=True,
                )
            except Exception as e:
                logger.error(f"[IntentStrategy] LLM invoke failed: {e}")
                yield self.create_text_message(text=f"LLM 呼叫失敗：{e}")
                return

            if chunks is None:
                logger.error("[IntentStrategy] LLM invoke returned None")
                yield self.create_text_message(text="LLM 回應為空，請檢查模型設定。")
                return

            response_text = ""
            tool_calls = []

            for chunk in chunks:
                if chunk.delta.message and chunk.delta.message.content:
                    c = chunk.delta.message.content
                    if isinstance(c, list):
                        for item in c:
                            response_text += item.data
                    else:
                        response_text += str(c)
                if (chunk.delta.message
                        and hasattr(chunk.delta.message, "tool_calls")
                        and chunk.delta.message.tool_calls):
                    for tc in chunk.delta.message.tool_calls:
                        tool_calls.append((
                            tc.id,
                            tc.function.name,
                            json.loads(tc.function.arguments) if tc.function.arguments else {},
                        ))

            logger.info(f"[IntentStrategy] Iteration {iteration+1}: text={len(response_text)} chars, tool_calls={[n for _,n,_ in tool_calls]}")

            if not tool_calls:
                seq += 1; trace.append(self._trace_entry(seq, A_LLM, A_PLUGIN, "回傳最終回覆", f"{len(response_text)} 字元"))
                seq += 1; trace.append(self._trace_entry(seq, A_PLUGIN, A_DIFY, "回傳最終回覆"))
                seq += 1; trace.append(self._trace_entry(seq, A_DIFY, A_USER, "顯示回覆"))
                yield self.finish_log_message(log=log_iter, data={"回覆預覽": response_text[:300]})
                break

            # ── LLM 要求呼叫工具 → 直接呼叫 n8n ──
            tool_names_called = [name for _, name, _ in tool_calls]
            seq += 1; trace.append(self._trace_entry(seq, A_LLM, A_PLUGIN, "需要呼叫工具", ", ".join(tool_names_called)))

            yield self.finish_log_message(log=log_iter, data={
                "結果": f"需呼叫 {len(tool_calls)} 個工具", "工具": tool_names_called,
            })

            messages.append(AssistantPromptMessage(
                content=response_text,
                tool_calls=[{
                    "id": tc_id, "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                } for tc_id, name, args in tool_calls],
            ))

            for tc_id, name, args in tool_calls:
                tool_def = tool_def_map.get(name)
                if not tool_def:
                    result = f"Tool not found: {name}"
                    seq += 1; trace.append(self._trace_entry(seq, A_PLUGIN, A_N8N, "回傳錯誤", result))
                    messages.append(ToolPromptMessage(content=result, tool_call_id=tc_id, name=name))
                    continue

                seq += 1; trace.append(self._trace_entry(
                    seq, A_PLUGIN, A_N8N, f"請執行 {name}",
                    json.dumps(args, ensure_ascii=False)[:80],
                ))

                log_tool = self.create_log_message(
                    label=f"[系統執行員 → n8n] {name}",
                    data={"工具": name, "參數": args},
                    status=ToolInvokeMessage.LogMessage.LogStatus.START,
                )
                yield log_tool

                # 組裝 payload 並呼叫 n8n
                payload = _build_payload(tool_def, args)
                result, raw_bytes = _call_n8n(tool_def["endpoint"], tool_def["method"], payload, n8n_base_url)

                # 若回傳圖片，上傳至 Dify file storage 並輸出 image message
                if raw_bytes is not None:
                    _debug_log(f"raw_bytes detected, size={len(raw_bytes)}")
                    try:
                        # SDK file.upload() 的 signed URL 缺少 host（SDK bug）
                        # signed URL 的目標是 Dify API（非 daemon），用 DIFY_INNER_API_URL
                        import os as _os
                        from dify_plugin.core.entities.invocation import InvokeType
                        api_base = _os.environ.get("DIFY_INNER_API_URL", "").rstrip("/") or _os.environ.get("PLUGIN_DIFY_INNER_API_URL", "").rstrip("/") or "http://api:5001"
                        signed_url = None
                        for resp_chunk in self.session.file._backwards_invoke(
                            InvokeType.UploadFile, dict,
                            {"filename": f"{name}.png", "mimetype": "image/png"},
                        ):
                            signed_url = resp_chunk.get("url", "")
                            break

                        if not signed_url:
                            _debug_log("upload failed: no signed URL")
                        else:
                            # 若 URL 是相對路徑，補上 Dify API base URL
                            if signed_url.startswith("/"):
                                signed_url = f"{api_base}{signed_url}"
                            _debug_log(f"uploading to: {signed_url[:120]}")

                            upload_resp = http_requests.post(
                                signed_url,
                                files={"file": (f"{name}.png", raw_bytes, "image/png")},
                                timeout=30,
                            )
                            _debug_log(f"upload response: status={upload_resp.status_code} body={upload_resp.text[:300]}")

                            if upload_resp.status_code == 201:
                                file_data = upload_resp.json()
                                preview_url = file_data.get("preview_url", "")
                                # preview_url 是相對路徑，需補上完整 URL 讓 Dify 能下載
                                if preview_url.startswith("/"):
                                    preview_url = f"{api_base}{preview_url}"
                                _debug_log(f"upload OK: id={file_data.get('id')} preview_url={preview_url}")
                                if preview_url:
                                    yield self.create_image_message(image_url=preview_url)
                    except Exception as e:
                        import traceback
                        _debug_log(f"圖片上傳失敗：{e}\n{traceback.format_exc()}")

                seq += 1; trace.append(self._trace_entry(seq, A_N8N, A_PLUGIN, f"回傳 {name} 結果", result[:60]))
                yield self.finish_log_message(log=log_tool, data={"結果": result[:500]})
                messages.append(ToolPromptMessage(content=result, tool_call_id=tc_id, name=name))

        # ── 完成 ──
        trace_text = self._trace_to_text(trace)
        yield self.finish_log_message(log=log_main, data={
            "總迭代": iteration + 1,
            "回覆長度": f"{len(response_text)} 字元",
            "執行模式": params.execution_mode,
            "狀態": "完成",
            "sequence_trace": trace_text,
        })
        yield self.create_text_message(text=response_text)
