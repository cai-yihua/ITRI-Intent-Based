#!/bin/bash
# =============================================
# Dify Plugin 打包 + 安裝腳本
# intent-agent v5.0
# 適用 Dify 1.13.x（Cookie + CSRF 認證）
# =============================================
set -e

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PKG_FILE="/tmp/intent-agent.difypkg"

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

# Dify docker 目錄（用於 docker compose exec）
DIFY_DOCKER_DIR="${DIFY_DOCKER_DIR:-$(cd "$PLUGIN_DIR/../../../dify/docker" 2>/dev/null && pwd || echo "")}"

echo "=== Dify Plugin Installer ==="
echo "Dify URL: $DIFY_URL"
echo "Plugin:   $PLUGIN_DIR"
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
export DIFY_URL EMAIL PASSWORD PLUGIN_DIR PKG_FILE DIFY_DOCKER_DIR

# --- 全部用 Python 執行（避免 shell cookie 處理問題）---
python3 << 'PYTHON_SCRIPT'
import zipfile, os, sys, time, json, base64, subprocess

# ── 參數 ──
plugin_dir = os.environ.get("PLUGIN_DIR", ".")
pkg_file = os.environ.get("PKG_FILE", "/tmp/intent-agent.difypkg")
dify_url = os.environ.get("DIFY_URL", "http://localhost:30000")
email = os.environ.get("EMAIL", "")
password = os.environ.get("PASSWORD", "")
dify_docker_dir = os.environ.get("DIFY_DOCKER_DIR", "")

try:
    import requests
except ImportError:
    print("  [ERROR] requests library not found. Run: pip install requests")
    sys.exit(1)

# ── Step 1: 打包 ──
print("[1/4] Packaging plugin...")
os.chdir(plugin_dir)
with zipfile.ZipFile(pkg_file, 'w', zipfile.ZIP_DEFLATED) as zf:
    for dp, dns, fns in os.walk('.'):
        dns[:] = [d for d in dns if d not in ('__pycache__', '.git', 'scripts')]
        for f in fns:
            if f.endswith('.pyc'):
                continue
            full = os.path.join(dp, f)
            zf.write(full, os.path.relpath(full, '.'))
print(f"  -> {pkg_file} ({os.path.getsize(pkg_file)/1024:.1f} KB)")

# ── Step 2: 登入 ──
print("[2/4] Logging in...")
pw_b64 = base64.b64encode(password.encode()).decode()
session = requests.Session()
login_resp = session.post(f"{dify_url}/console/api/login", json={
    "email": email, "password": pw_b64, "language": "zh-Hant", "remember_me": True,
})
if login_resp.json().get("result") != "success":
    print(f"  [ERROR] Login failed: {login_resp.text[:200]}")
    sys.exit(1)
csrf = session.cookies.get("csrf_token", "")
print("  -> Login OK")

# ── Step 3: 上傳 ──
print("[3/4] Uploading plugin...")
with open(pkg_file, 'rb') as f:
    upload_resp = session.post(
        f"{dify_url}/console/api/workspaces/current/plugin/upload/pkg",
        headers={"X-CSRF-Token": csrf},
        files={"pkg": ("intent-agent.difypkg", f, "application/octet-stream")},
    )
upload_data = upload_resp.json()
plugin_uid = upload_data.get("unique_identifier", "")
if not plugin_uid:
    print(f"  [ERROR] Upload failed: {upload_resp.text[:300]}")
    sys.exit(1)
print(f"  -> UID: {plugin_uid}")

# ── Step 4: 安裝（透過 docker compose exec 繞過 Dify API decode bug）──
print("[4/4] Installing plugin...")

if not dify_docker_dir or not os.path.isdir(dify_docker_dir):
    print(f"  [WARN] Dify docker dir not found: {dify_docker_dir}")
    print("  Trying console API fallback...")
    # Fallback: 嘗試 console API
    install_resp = session.post(
        f"{dify_url}/console/api/workspaces/current/plugin/install/pkg",
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
        json={"plugin_unique_identifiers": [plugin_uid]},
    )
    print(f"  {install_resp.status_code}: {install_resp.text[:200]}")
else:
    # 取得 tenant_id（從 profile API）
    profile = session.get(f"{dify_url}/console/api/account/profile", headers={"X-CSRF-Token": csrf}).json()
    tenant_id = ""
    # 嘗試多種欄位
    for key in ("current_tenant_id", "tenant_id"):
        if profile.get(key):
            tenant_id = profile[key]
            break
    if not tenant_id:
        # 從 tenants 列表取
        tenants = session.get(f"{dify_url}/console/api/workspaces", headers={"X-CSRF-Token": csrf}).json()
        if isinstance(tenants, list) and tenants:
            tenant_id = tenants[0].get("id", "")
        elif isinstance(tenants, dict):
            for t in tenants.get("data", tenants.get("workspaces", [])):
                tenant_id = t.get("id", "")
                break

    if not tenant_id:
        print(f"  [ERROR] Cannot determine tenant_id. Profile: {json.dumps(profile)[:200]}")
        sys.exit(1)

    # 透過 docker compose exec api 直接呼叫 daemon install
    install_cmd = [
        "docker", "compose", "exec", "-T", "api", "python3", "-c",
        f"import httpx,os;key=os.environ.get('PLUGIN_DAEMON_KEY','');"
        f"r=httpx.post('http://plugin_daemon:5002/plugin/{tenant_id}/management/install/identifiers',"
        f"headers={{'X-Api-Key':key,'Content-Type':'application/json'}},"
        f"json={{'plugin_unique_identifiers':['{plugin_uid}'],'source':'package','metas':[{{}}]}});"
        f"print(f'{{r.status_code}}:{{r.text[:200]}}')",
    ]
    result = subprocess.run(install_cmd, capture_output=True, text=True, cwd=dify_docker_dir, timeout=30)
    output = result.stdout.strip()

    if output.startswith("200:"):
        task_data = json.loads(output[4:])
        task_id = task_data.get("data", {}).get("task_id", "")
        if task_data.get("data", {}).get("all_installed"):
            print("  -> Installed successfully!")
        elif task_id:
            print(f"  -> Install task: {task_id}")
            print("  Waiting for completion...")
            # 等待安裝完成（檢查 plugin list）
            for i in range(15):
                time.sleep(2)
                pl = session.get(
                    f"{dify_url}/console/api/workspaces/current/plugin/list",
                    headers={"X-CSRF-Token": csrf},
                ).json()
                found = any("intent-agent" in p.get("plugin_id", "") for p in pl.get("plugins", []))
                if found:
                    print("  -> Installed successfully!")
                    break
            else:
                print("  [WARN] Timeout waiting. Check Dify Plugins page.")
        else:
            print(f"  -> Response: {output}")
    else:
        print(f"  [ERROR] Install failed: {output}")
        if result.stderr:
            print(f"  stderr: {result.stderr[:200]}")
        sys.exit(1)

# 清理
os.remove(pkg_file)
print("")
print(f"Done! Go to {dify_url} -> Plugins to verify.")
PYTHON_SCRIPT
