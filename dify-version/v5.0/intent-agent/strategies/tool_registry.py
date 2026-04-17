"""
Tool Registry：集中管理所有工具的技術細節。

職責分工：
- 工具的「描述」（給 LLM 看的）→ 在 prompts/prompt.md 中維護
- 工具的「技術參數」（給程式用的）→ 在本檔案中維護

包含：
1. TOOL_SCHEMAS    : 給 LLM 的 function calling schema（name + description + parameters）
2. ENDPOINTS       : 工具名稱 → n8n webhook UUID
3. METHODS         : 工具名稱 → HTTP method
4. RISKY_TOOLS     : 高風險工具集合（step-by-step 模式下需確認）
5. QUERY_TOOLS     : 查詢類工具集合（永遠直接執行）
6. build_payload() : 根據工具名稱組裝請求 payload
7. call_n8n_with_retry() : 呼叫 n8n webhook，含 3 次重試
"""
import json
import logging
import time
from typing import Any

import requests as http_requests

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# 工具 Function Calling Schema
# ─────────────────────────────────────────
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_ue_status",
        "description": (
            "查詢 UE 狀態。參數互斥，依優先級擇一使用："
            "1. ueid（指定 UE ID，純數字字串）"
            "2. location（地點代碼：電梯前=131, 501走廊=132, 503會議室=135）"
            "3. all_edge=true（所有受干擾 UE）"
            "4. all_center=true（所有未受干擾 UE）"
            "5. worst_part（效能最差比例，0.1=最差10%）"
            "6. 全部不填=查詢所有 UE"
        ),
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
    },
    {
        "name": "get_sinr_map",
        "description": "查詢場域的 SINR 熱力圖，無需任何參數。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_active_rapp_status",
        "description": (
            "查詢目前場域中正在執行的優化狀態（rApp）。用於確認是否有優化正在運行。"
            "執行 enable_im/simulate_im/enable_qoe/simulate_qoe 前必須先調用此工具檢查。無需任何參數。"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "enable_im",
        "description": (
            "開啟干擾管理(IM)優化。執行前必須先調用 get_active_rapp_status 確認無優化正在執行。"
            "參數互斥，依優先級擇一使用："
            "1. location（地點代碼：電梯前=131, 501走廊=132, 503會議室=135）"
            "2. all_edge=true（所有受干擾 UE）"
            "3. all_center=true（所有未受干擾 UE）"
            "4. worst_part（效能最差比例，0.1=最差10%）"
            "5. 全部不填=優化整個場域。"
            "可選 optimization_inc_percent（提升比例，預設0.1即10%）。"
        ),
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
    },
    {
        "name": "disable_im",
        "description": "關閉干擾管理(IM)優化。無需任何參數。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "simulate_im",
        "description": (
            "模擬干擾管理(IM)優化效果，不會實際啟用。"
            "執行前必須先調用 get_active_rapp_status 確認無優化正在執行。參數同 enable_im。"
        ),
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
    },
    {
        "name": "enable_qoe",
        "description": (
            "針對特定 UE 啟用 QoE 優化。執行前必須先調用 get_active_rapp_status 確認無優化正在執行。"
            "必須提供 ueid 參數。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ueid": {"type": "string", "description": "目標 UE ID（純數字字串）"},
                "optimization_inc_percent": {"type": "number", "description": "提升比例，預設0.1即10%"},
            },
            "required": ["ueid"],
        },
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
    },
    {
        "name": "simulate_qoe",
        "description": (
            "模擬特定 UE 的 QoE 優化效果，不會實際啟用。"
            "執行前必須先調用 get_active_rapp_status 確認無優化正在執行。必須提供 ueid 參數。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ueid": {"type": "string", "description": "目標 UE ID（純數字字串）"},
                "optimization_inc_percent": {"type": "number", "description": "提升比例，預設0.1即10%"},
            },
            "required": ["ueid"],
        },
    },
]

# ─────────────────────────────────────────
# 工具 → n8n webhook UUID
# ─────────────────────────────────────────
ENDPOINTS: dict[str, str] = {
    "get_ue_status": "e8f1cc4d-7560-4ae6-8ec2-dece817160be",
    "get_sinr_map": "9a74bbcd-861d-4c03-b3b4-1410c6dddb08",
    "get_active_rapp_status": "e36fcc1c-bf54-4590-a894-c00bcb5c318c",
    "enable_im": "7aa374ea-d239-462c-987e-42872a27133e",
    "disable_im": "0ea63998-8332-4626-9452-34c923fa538e",
    "simulate_im": "da0e5281-0fbc-4c30-adb6-76b23824ec28",
    "enable_qoe": "fbc98177-6e62-4738-9e47-2312896ff5da",
    "disable_qoe": "009cca49-effc-492a-b985-e14e0a95b133",
    "simulate_qoe": "7aa374ea-d239-462c-987e-42872a27133e",
}

# ─────────────────────────────────────────
# 工具 → HTTP method
# ─────────────────────────────────────────
METHODS: dict[str, str] = {
    "get_ue_status": "POST",
    "get_sinr_map": "POST",
    "get_active_rapp_status": "POST",
    "enable_im": "POST",
    "disable_im": "GET",
    "simulate_im": "POST",
    "enable_qoe": "POST",
    "disable_qoe": "POST",
    "simulate_qoe": "POST",
}

# ─────────────────────────────────────────
# 工具分級
# ─────────────────────────────────────────
QUERY_TOOLS: frozenset[str] = frozenset({
    "get_ue_status",
    "get_sinr_map",
    "get_active_rapp_status",
})

RISKY_TOOLS: frozenset[str] = frozenset({
    "enable_im",
    "disable_im",
    "simulate_im",
    "enable_qoe",
    "disable_qoe",
    "simulate_qoe",
})

# ─────────────────────────────────────────
# Payload 建構
# ─────────────────────────────────────────
IM_BASE_PAYLOAD: dict[str, Any] = {
    "manualNumOfNonInterferedRb": -1,
    "percentOfEdgeUeNumForGtCaseBound": 0.5,
    "percentOfEdgeUeNumForLtCaseBound": 0.3,
    "percentOfCenterUeAvgTputForGtCase": 0.75,
    "percentOfCenterUeAvgTputForEqCase": 0.75,
    "percentOfCenterUeAvgTputForLtCase": 0.75,
}

LOCATION_MAP: dict[str, dict[str, Any]] = {
    "131": {"filter": {"location": "131"}, "optimization_method": 3},
    "132": {"filter": {"location": "132"}, "optimization_method": 3},
    "135": {"filter": {"location": "135"}, "optimization_method": 3},
}


def _build_im_payload(args: dict[str, Any]) -> dict[str, Any]:
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


def build_payload(tool_name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """根據工具名稱組裝請求 payload。"""
    if tool_name in {"enable_im", "simulate_im"}:
        return _build_im_payload(args)

    if tool_name in {"enable_qoe", "simulate_qoe"}:
        return {
            "ueid": str(args.get("ueid", "")),
            "optimization_inc_percent": float(args.get("optimization_inc_percent", 0.1)),
        }

    if tool_name == "disable_qoe":
        return {"ueid": str(args.get("ueid", ""))}

    if tool_name == "disable_im":
        return None

    return args if args else None


# ─────────────────────────────────────────
# n8n 呼叫 + 重試機制
# ─────────────────────────────────────────
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 1.5  # 秒

# 預設 timeout（秒）
DEFAULT_TIMEOUT = 60

# 工具專屬 timeout（覆蓋預設值）
TOOL_TIMEOUTS: dict[str, int] = {
    # 圖片回傳類工具，n8n 可能需要較久才能產圖
    "get_sinr_map": 30,
    # 優化類工具，n8n 可能需要呼叫多個外部 API
    "enable_im": 30,
    "simulate_im": 30,
    "enable_qoe": 30,
    "simulate_qoe": 30,
    "disable_im": 30,
    "disable_qoe": 30,
}


def call_n8n_with_retry(
    tool_name: str,
    payload: dict[str, Any] | None,
    n8n_base_url: str,
    timeout: int | None = None,
) -> tuple[str, bytes | None, dict[str, Any]]:
    """
    呼叫 n8n webhook，失敗時重試最多 3 次。

    回傳：(text_for_llm, raw_bytes_or_None, meta)
    meta 包含：attempts, elapsed_ms, status_code, error
    """
    endpoint = ENDPOINTS.get(tool_name)
    method = METHODS.get(tool_name, "POST")
    if not endpoint:
        return f"Unknown tool: {tool_name}", None, {"attempts": 0, "error": "unknown_tool"}

    effective_timeout = timeout if timeout is not None else TOOL_TIMEOUTS.get(tool_name, DEFAULT_TIMEOUT)
    url = f"{n8n_base_url.rstrip('/')}/{endpoint}"
    last_error: str | None = None
    start_time = time.time()

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                "[n8n] %s %s attempt=%d timeout=%ds payload=%s",
                method, url, attempt, effective_timeout,
                json.dumps(payload, ensure_ascii=False)[:200] if payload else "None",
            )
            resp = http_requests.request(
                method=method,
                url=url,
                json=payload if method.upper() in ("POST", "PUT", "PATCH") else None,
                timeout=effective_timeout,
            )
            elapsed_ms = int((time.time() - start_time) * 1000)
            content_type = resp.headers.get("Content-Type", "")
            meta = {
                "attempts": attempt,
                "elapsed_ms": elapsed_ms,
                "status_code": resp.status_code,
                "content_type": content_type,
            }

            if resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                logger.warning("[n8n] server error attempt=%d: %s", attempt, last_error)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_BASE ** (attempt - 1))
                    continue
                meta["error"] = last_error
                return f"n8n server error after {attempt} attempts: {last_error}", None, meta

            if content_type.startswith("image/"):
                return (
                    "[圖片已回傳，請描述此圖片為 SINR 熱力圖，已直接顯示給使用者]",
                    resp.content,
                    meta,
                )

            return resp.text, None, meta

        except http_requests.RequestException as e:
            last_error = str(e)
            logger.warning("[n8n] request failed attempt=%d: %s", attempt, last_error)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_BASE ** (attempt - 1))
                continue

    elapsed_ms = int((time.time() - start_time) * 1000)
    return (
        f"Error calling n8n after {MAX_RETRIES} attempts: {last_error}",
        None,
        {"attempts": MAX_RETRIES, "elapsed_ms": elapsed_ms, "error": last_error},
    )
