import requests


def call_n8n(
    credentials: dict,
    endpoint_path: str,
    method: str = "POST",
    payload: dict | None = None,
    timeout: int = 30,
) -> str:
    """共用的 n8n webhook HTTP 呼叫封裝。"""
    base_url = credentials.get("n8n_base_url", "").rstrip("/")
    url = f"{base_url}/{endpoint_path}"
    resp = requests.request(
        method=method,
        url=url,
        json=payload if method.upper() in ("POST", "PUT", "PATCH") else None,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.text


# ──────────────────────────────────────��──
# n8n Webhook Endpoint 對照表
# ─────────────────────────────────────────
ENDPOINTS = {
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
# IM Payload 組裝（enable_im / simulate_im 共用）
# ─────────────────────────────────────────
# 地點 → filter + optimization_method 映射
LOCATION_MAP = {
    "131": {"filter_payload": {"location": "131"}, "optimization_method": 3},
    "132": {"filter_payload": {"location": "132"}, "optimization_method": 3},
    "135": {"filter_payload": {"location": "135"}, "optimization_method": 3},
}
# 502走廊特殊：all_edge
ALL_EDGE_OPTIMIZATION_METHOD = 2


def build_im_payload(params: dict) -> dict:
    """組裝 enable_im / simulate_im 的完整 payload。"""
    # 基礎模板
    payload = {
        "manualNumOfNonInterferedRb": -1,
        "percentOfEdgeUeNumForGtCaseBound": 0.5,
        "percentOfEdgeUeNumForLtCaseBound": 0.3,
        "percentOfCenterUeAvgTputForGtCase": 0.75,
        "percentOfCenterUeAvgTputForEqCase": 0.75,
        "percentOfCenterUeAvgTputForLtCase": 0.75,
    }

    # optimization_inc_percent
    payload["optimization_inc_percent"] = str(params.get("optimization_inc_percent", 0.1))

    # filter + optimization_method + optimization_param（依優先級）
    location = params.get("location")
    all_edge = params.get("all_edge")
    all_center = params.get("all_center")
    worst_part = params.get("worst_part")

    if location and location in LOCATION_MAP:
        loc = LOCATION_MAP[location]
        payload["filter"] = loc["filter_payload"]
        payload["optimization_method"] = loc["optimization_method"]
        payload["optimization_param"] = None
    elif all_edge:
        payload["filter"] = {"all_edge": True}
        payload["optimization_method"] = ALL_EDGE_OPTIMIZATION_METHOD
        payload["optimization_param"] = None
    elif all_center:
        payload["filter"] = {"all_center": True}
        payload["optimization_method"] = 3
        payload["optimization_param"] = None
    elif worst_part is not None:
        wp = float(worst_part)
        payload["filter"] = {"worst_part": wp}
        payload["optimization_method"] = 6
        payload["optimization_param"] = {"worst_percent": wp}
    else:
        # 預設：全場域
        payload["filter"] = {}
        payload["optimization_method"] = 4
        payload["optimization_param"] = None

    return payload
