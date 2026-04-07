#!/bin/bash
# =============================================
# Dify Workflow 自動建立腳本
# 建立 advanced-chat App：開始 → Agent → 回覆
# 適用 Dify 1.13.x（Cookie + CSRF 認證）
# =============================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_NAME="${APP_NAME:-ITRI O-RAN Intent Agent v5.0}"

# --- 載入 .env ---
ENV_FILE="${ENV_FILE:-$(cd "$PLUGIN_DIR/../../.." && pwd)/.env}"
if [ -f "$ENV_FILE" ]; then
  echo "Loading .env from: $ENV_FILE"
  set -a
  source "$ENV_FILE"
  set +a
fi

# 從 DIFY_LOGIN_URL 推導 DIFY_URL
if [ -n "$DIFY_LOGIN_URL" ] && [ -z "$DIFY_URL" ]; then
  DIFY_URL="${DIFY_LOGIN_URL%/console/api/login}"
fi
DIFY_URL="${DIFY_URL:-http://localhost:30000}"
EMAIL="${DIFY_EMAIL:-$EMAIL}"
PASSWORD="${DIFY_PASSWORD:-$PASSWORD}"

echo "=== Dify Workflow Setup ==="
echo "Dify URL: $DIFY_URL"
echo ""

# --- 輸入帳密（若 .env 未提供）---
if [ -z "$EMAIL" ]; then
  read -p "Dify Email: " EMAIL
fi
if [ -z "$PASSWORD" ]; then
  read -s -p "Dify Password: " PASSWORD
  echo ""
fi

# Export 變數給 Python heredoc
export DIFY_URL EMAIL PASSWORD APP_NAME N8N_BASE_URL

# --- 全部用 Python 執行 ---
python3 << 'PYTHON_SCRIPT'
import sys, json, base64

try:
    import requests
except ImportError:
    print("  [ERROR] requests library not found. Run: pip install requests")
    sys.exit(1)

import os
dify_url = os.environ.get("DIFY_URL", "http://localhost:30000")
email = os.environ.get("EMAIL", "")
password = os.environ.get("PASSWORD", "")
app_name = os.environ.get("APP_NAME", "ITRI O-RAN Intent Agent v5.0")

# ── Step 1: 登入 ──
print("[1/5] 登入...")
pw_b64 = base64.b64encode(password.encode()).decode()
session = requests.Session()
r = session.post(f"{dify_url}/console/api/login", json={
    "email": email, "password": pw_b64, "language": "zh-Hant", "remember_me": True,
})
if r.json().get("result") != "success":
    print(f"  [ERROR] Login failed: {r.text[:200]}")
    sys.exit(1)
csrf = session.cookies.get("csrf_token", "")
headers = {"Content-Type": "application/json", "X-CSRF-Token": csrf}
print("  -> Login OK")

# ── Step 2: 偵測 Plugin ──
print("[2/5] 偵測 Intent Agent Plugin...")
pl = session.get(f"{dify_url}/console/api/workspaces/current/plugin/list", headers=headers).json()
plugin_uid = ""
for p in pl.get("plugins", []):
    if "intent-agent" in p.get("plugin_id", ""):
        plugin_uid = p["plugin_unique_identifier"]
        break
if not plugin_uid:
    print("  [ERROR] Intent Agent plugin not found. Run install.sh first.")
    sys.exit(1)
print(f"  -> Plugin: {plugin_uid}")

# ── Step 3: 建立 App ──
print(f"[3/5] 建立 App: {app_name}...")
r = session.post(f"{dify_url}/console/api/apps", headers=headers, json={
    "name": app_name, "mode": "advanced-chat",
    "icon_type": "emoji", "icon": "📡", "icon_background": "#D5F5F6",
})
app_data = r.json()
app_id = app_data.get("id", "")
if not app_id:
    print(f"  [ERROR] Failed to create app: {r.text[:200]}")
    sys.exit(1)
print(f"  -> App ID: {app_id}")

# ── Step 4: 設定 Workflow ──
print("[4/5] 設定 Workflow...")

# 取得初始 draft hash
try:
    draft = session.get(f"{dify_url}/console/api/apps/{app_id}/workflows/draft", headers=headers).json()
    current_hash = draft.get("hash", "")
except Exception:
    current_hash = ""

agent_id = "agent_001"
payload = {
    "graph": {
        "nodes": [
            {
                "id": "start", "type": "custom",
                "data": {"type": "start", "title": "開始", "variables": []},
                "position": {"x": 80, "y": 282},
                "sourcePosition": "right", "targetPosition": "left",
                "width": 242, "height": 71,
            },
            {
                "id": agent_id, "type": "custom",
                "data": {
                    "type": "agent",
                    "title": "ITRI O-RAN Intent Agent",
                    "selected": False,
                    "agent_strategy_provider_name": "yc/intent-agent/intent_agent",
                    "agent_strategy_name": "intent_strategy",
                    "agent_strategy_label": "Intent-Based Strategy",
                    "output_schema": {},
                    "plugin_unique_identifier": plugin_uid,
                    "agent_parameters": {
                        "query": {"type": "constant", "value": "{{#sys.query#}}"},
                        "model": {
                            "type": "constant",
                            "value": {
                                "provider": "langgenius/gemini/google",
                                "model": "gemini-2.5-flash",
                                "model_type": "llm",
                                "mode": "chat",
                                "type": "model-selector",
                                "completion_params": {},
                            },
                        },
                        "n8n_base_url": {"type": "constant", "value": os.environ.get("N8N_BASE_URL", "http://172.27.94.1:5678").rstrip("/") + "/webhook"},
                        "execution_mode": {"type": "constant", "value": "auto"},
                        "maximum_iterations": {"type": "constant", "value": 5},
                        "tools": {"type": "constant", "value": []},
                    },
                },
                "position": {"x": 380, "y": 282},
                "sourcePosition": "right", "targetPosition": "left",
                "width": 241, "height": 134,
            },
            {
                "id": "answer", "type": "custom",
                "data": {
                    "type": "answer", "title": "直接回覆",
                    "answer": "{{#" + agent_id + ".text#}}",
                    "variables": [],
                    "files": [{"type": "image", "variable_selector": [agent_id, "files"]}],
                },
                "position": {"x": 680, "y": 282},
                "sourcePosition": "right", "targetPosition": "left",
                "width": 241, "height": 101,
            },
        ],
        "edges": [
            {
                "id": "e1", "type": "custom", "source": "start", "target": agent_id,
                "sourceHandle": "source", "targetHandle": "target", "zIndex": 0,
                "data": {"isInLoop": False, "sourceType": "start", "targetType": "agent"},
            },
            {
                "id": "e2", "type": "custom", "source": agent_id, "target": "answer",
                "sourceHandle": "source", "targetHandle": "target", "zIndex": 0,
                "data": {"isInLoop": False, "sourceType": "agent", "targetType": "answer"},
            },
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    },
    "features": {},
    "environment_variables": [],
    "conversation_variables": [],
}
if current_hash:
    payload["hash"] = current_hash

r = session.post(f"{dify_url}/console/api/apps/{app_id}/workflows/draft", headers=headers, json=payload)
if r.status_code == 200 and r.json().get("result") == "success":
    print("  -> Workflow 設定完成")
else:
    print(f"  [ERROR] Workflow sync failed: {r.text[:300]}")
    sys.exit(1)

# ── Step 5: 發佈 ──
print("[5/5] 發佈 Workflow...")
r = session.post(f"{dify_url}/console/api/apps/{app_id}/workflows/publish", headers=headers, json={})
if r.status_code == 200:
    print("  -> 發佈成功")
else:
    print(f"  [WARN] Publish: {r.text[:200]}")

print("")
print("=" * 44)
print("Done!")
print(f"App: {app_name}")
print(f"URL: {dify_url}/app/{app_id}/workflow")
print("")
print("Workflow: 開始 → ITRI O-RAN Intent Agent → 回覆")
print("Model: gemini-2.5-flash")
print("Execution Mode: auto (default)")
print("Prompt: prompts/prompt.md (in plugin)")
print("=" * 44)
PYTHON_SCRIPT
