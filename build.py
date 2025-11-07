from __future__ import annotations
import os, sys, json, time, subprocess, requests, logging, mimetypes, re
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager
from dotenv import load_dotenv, set_key
from typing import List, TypedDict
from tenacity import retry, stop_after_attempt, wait_fixed
import docker
from docker.errors import NotFound, APIError
from concurrent.futures import ThreadPoolExecutor, as_completed

# ────────────────── 日誌路徑與檔名 ──────────────────
LOG_DIR = Path("log")
LOG_DIR.mkdir(exist_ok=True)

_NOW_STR = datetime.now().strftime("%Y%m%d_%H%M")
LOG_FILE = LOG_DIR / f"{_NOW_STR}.log"


# ────────────────── Logging 設定 ──────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s| %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ],
)

def log_error(msg: str):
    logging.error(msg)
    raise RuntimeError(msg)

# ────────────────── 共用工具 ──────────────────
dotenv_path = os.path.abspath("./Backend/.env")
load_dotenv(dotenv_path=dotenv_path, override=True)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
HTTP_DIFY_HOST = os.getenv("HTTP_DIFY_HOST")

dotenv_path = os.path.abspath("./Dashboard/.env")
load_dotenv(dotenv_path=dotenv_path, override=True)
PROTOCAL = os.getenv("PROTOCAL")
HOST = os.getenv("HOST")
API_PORT = os.getenv("API_PORT")
API_ROOT = os.getenv("API_ROOT")
API_VERSION = os.getenv("API_VERSION")

dotenv_path = os.path.abspath(".env")
load_dotenv(dotenv_path=dotenv_path, override=True)

SUDO_PASSWORD = os.getenv("SUDO_PASSWORD")
N8N_EXIST = os.getenv("N8N_EXIST")

# n8n
N8N_EMAIL = os.getenv("N8N_EMAIL")
N8N_PASSWORD = os.getenv("N8N_PASSWORD")
N8N_FIRSTNAME = os.getenv("N8N_FIRSTNAME")
N8N_LASTNAME = os.getenv("N8N_LASTNAME")
N8N_BASE_URL = os.getenv("N8N_BASE_URL")
N8N_SETUP_URL = os.getenv("N8N_SETUP_URL")
N8N_LOGIN_URL = os.getenv("N8N_LOGIN_URL")
N8N_SURVEY_URL = os.getenv("N8N_SURVEY_URL")
N8N_GET_API_URL = os.getenv("N8N_GET_API_URL")
N8N_API_URL = os.getenv("N8N_API_URL")

# dify
DIFY_TAG = os.getenv("DIFY_TAG")
DIFY_EMAIL = os.getenv("DIFY_EMAIL")
DIFY_NAME = os.getenv("DIFY_NAME")
DIFY_PASSWORD = os.getenv("DIFY_PASSWORD")
DIFY_SETUP_URL = os.getenv("DIFY_SETUP_URL")
DIFY_LOGIN_URL = os.getenv("DIFY_LOGIN_URL")
DIFY_IMPORT_URL = os.getenv("DIFY_IMPORT_URL")
DIFY_BRANCH = os.getenv("DIFY_BRANCH")
API_KEY_BASE = os.getenv("API_KEY_BASE")

DIFY_ADD_MODELS_VENDOR = os.getenv("DIFY_ADD_MODELS_VENDOR")
DIFY_SET_OPENAI_API_KEY = os.getenv("DIFY_SET_OPENAI_API_KEY")
DIFY_UPLOAD_TXT = os.getenv("DIFY_UPLOAD_TXT")
DIFY_INIT_DB = os.getenv("DIFY_INIT_DB")
DIFY_BASE = os.getenv("DIFY_BASE")

DIFY_CONTAINERS: List[str] = [
    "docker-nginx-1",
    "docker-worker-1",
    "docker-api-1",
    "docker-ssrf_proxy-1",
    "docker-weaviate-1",
    "docker-sandbox-1",
    "docker-web-1",
]

DIFY_CONTAINERS_HEALTHY: List[str] = [
    "docker-db-1",
    "docker-redis-1",
    "docker-sandbox-1",
]

BACKEND_CONTAINERS: List[str] = [
    "itri-intent-backend",
    "intent-postgres-db",
    "intent-redis-db",
    "intent-pgadmin"
]

class JSONPayload(TypedDict):
    mode: str
    json_payload: dict[str]

class YamlPayload(TypedDict):
    mode: str
    yaml_content: str

@contextmanager
def step_timer(label: str):
    """
    自動把花費時間寫進 ./log/… 內
    """
    logging.info(f"🚩 開始 - {label}")
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        logging.info(f"🏁 結束 - {label}，耗時 {elapsed:,.2f} s")

@retry(stop=stop_after_attempt(6), wait=wait_fixed(10))
def _run_with_retry(fn, *args, **kwargs):
    return fn(*args, **kwargs)

def run_shell_script(script_name):
    try:
        subprocess.run(["chmod", "+x", script_name], check=True)
        subprocess.run(["./" + script_name], check=True, text=True, timeout=500)
        logging.info(f"✅ {script_name} 執行成功")
    except Exception as e:
        log_error(f"❗ 未預期錯誤：{e}")

def wait_for_container_ready(containers: List[str], timeout: int = 50, require_healthy: bool = False):
    """
    等待一個或多個 container 全數進入就緒狀態。

    Args:
        containers      : container 名稱列表。
        timeout         : 每個 container 最多等待秒數。
        require_healthy : 若 image 有 HEALTHCHECK，是否必須等到 healthy。

    Returns:
        bool: 全部就緒→True；任何一個逾時/失敗→False。
    """
    client = docker.from_env()

    for name in containers:
        logging.info(f"⏳ '{name:25}' container 就緒中")

        for _ in range(timeout):
            try:
                container = client.containers.get(name)
                container.reload()
                state = container.attrs["State"]
                status = state.get("Status")                    # running / exited…
                health = state.get("Health", {}).get("Status")  # healthy / starting…

                if status == "running" and (
                    (not require_healthy) or (health == "healthy")
                ):
                    logging.info(f"✅ '{name:25}' container 已就緒")
                    break                                       # 進到下一個 container
            except NotFound:
                logging.error(f"⚠️ 找不到名稱為 '{name}' 的 container")
            except APIError as err:
                logging.error(f"⚠️ Docker API error: {err}")

            time.sleep(1)
        else:
            # for-loop 正常結束代表逾時
            logging.error(f"❌ container '{name}' 未在 {timeout}s 內就緒")

def ensure_docker_network(network_name: str = "itri-net"):
    try:
        exists = subprocess.run(
            ["docker", "network", "inspect", network_name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ).returncode == 0

        if exists:
            print(f"ℹ️  network 已存在，略過 create")
            return

        subprocess.run(["docker", "network", "create", network_name], check=True)
        print(f"✅ 建立 network 成功")

    except subprocess.CalledProcessError as e:
        print(f"❌ 建立 network 失敗：{e}")
        raise

# ────────────────── n8n ──────────────────
def n8n_setup_owner():
    """
    設定 owner 資訊
    """    
    payload = {
        "email": N8N_EMAIL,
        "firstName": N8N_FIRSTNAME,
        "lastName": N8N_LASTNAME,
        "password": N8N_PASSWORD
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    try:
        response = requests.post(N8N_SETUP_URL, json=payload, headers=headers)
        response.raise_for_status()
        result = response.json()

        if result.get("data"):
            logging.info("✅ 註冊成功")
        else:
            log_error("❌ 註冊失敗")

    except Exception as e:
        log_error(f"註冊錯誤：{e}")

def n8n_login() -> requests.Session:
    """
    登入並取得 auth_token
    """
    payload = {
        "email": N8N_EMAIL,
        "password": N8N_PASSWORD,
        "language": "zh-Hant",
        "remember_me": True
    }
    
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    session = requests.Session()

    try:
        response = session.post(N8N_LOGIN_URL, json=payload, headers=headers)
        response.raise_for_status()
        auth_token = response.cookies.get("n8n-auth")
        if auth_token:
            session.cookies.set("n8n-auth", auth_token)
            logging.info("✅ 登入成功")
        else:
            log_error("❌ 登入失敗")

        return session

    except Exception as e:
        log_error(f"登入錯誤：{e}")

def n8n_get_api_key(session) -> str:
    """
    獲取 API KEY獲取 API KEY
    """
    payload = {
        "expiresAt": None,
        "label": "test"
    }

    try:
        response = session.post(N8N_GET_API_URL, json=payload)
        response.raise_for_status()
        result = response.json()

        if result.get("data"):
            logging.info("✅ 獲取 API KEY 成功")
            api_key = result["data"].get("rawApiKey")
            return api_key
        else:
            log_error("❌ 獲取 API KEY 失敗")

    except Exception as e:
        log_error(f"獲取 API KEY 錯誤：{e}")

def json_to_payload() -> List[JSONPayload]:
    """
    尋找 /n8n-version/ 的所有 JSON 檔案，並將內容嵌入 JSON payload 中。
    """
    try:
        allowed_fields = ["name", "nodes", "connections", "settings", "staticData"]
        json_dir = os.path.join(os.getcwd(), 'n8n-version')
        payloads = []

        for filename in os.listdir(json_dir):
            if filename.endswith('.json'):
                file_path = os.path.join(json_dir, filename)
                with open(file_path, 'r', encoding='utf-8') as f:
                    text = f.read()
                text = re.sub("http://140.118.162.94:30000/api/v2/", f"{PROTOCAL}://{HOST}:{API_PORT}/{API_ROOT}/{API_VERSION}/", text, flags=re.IGNORECASE)
                json_content = json.loads(text)
                json_payload = {key: json_content[key] for key in allowed_fields if key in json_content}
                payload = {
                    "mode": "json-content",
                    "json_payload": json_payload
                }
                payloads.append(payload)

        logging.info("✅ 獲取 JSONs 成功")
        return payloads

    except Exception as e:
        log_error(f"獲取 JSON 錯誤：{e}")
    
def n8n_create_workflow(payloads) -> List[str]:
    """
    發送創建 workflow 的請求，回傳 workflow_id
    """
    try:
        workflow_ids = []
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-N8N-API-KEY": N8N_API_KEY
        }

        for payload in payloads:
            workflow_content = payload["json_payload"]

            response = requests.post(N8N_API_URL, json=workflow_content, headers=headers)
            if response.status_code == 200:
                logging.info("✅ 創建 workflow 成功")
                data = response.json()
                workflow_id = data.get("id")
                workflow_ids.append(workflow_id)

                # active workflow
                activate_url = f"{N8N_API_URL}/{workflow_id}/activate"
                activate_response = requests.post(activate_url, headers=headers)
                if activate_response.status_code == 200:
                    logging.info("✅ active workflow 成功")
                else:
                    log_error(f"⚠️ active workflow workflow_id = {workflow_id} 失敗")
            else:
                log_error(f"⚠️ 創建 workflow 失敗")
        
        return workflow_ids

    except Exception as e:
        log_error(f"創建 workflow 錯誤：{e}")


# ────────────────── dify ──────────────────
def dify_setup_owner():
    """
    設定 owner 資訊
    """    
    payload = {
        "email": DIFY_EMAIL,
        "name": DIFY_NAME,
        "password": DIFY_PASSWORD
    }

    try:
        response = requests.post(DIFY_SETUP_URL, json=payload)
        response.raise_for_status()
        result = response.json()

        if result.get("result") == "success":
            logging.info("✅ 註冊成功")
        else:
            log_error("❌ 註冊失敗")

    except Exception as e:
        log_error(f"註冊錯誤：{e}")

def dify_login_and_get_token() -> str:
    """
    登入並取得 access_token
    """
    payload = {
        "email": DIFY_EMAIL,
        "password": DIFY_PASSWORD,
        "language": "zh-Hant",
        "remember_me": True
    }

    try:
        response = requests.post(DIFY_LOGIN_URL, json=payload)
        response.raise_for_status()
        result = response.json()

        if result.get("result") == "success":
            token = result["data"]["access_token"]
            logging.info("✅ 成功登入")
            logging.info("✅ 成功取得 access token")
            return token
        else:
            log_error("❌ 登入失敗")

    except Exception as e:
        log_error(f"登入錯誤：{e}")

def yaml_to_payload() -> YamlPayload:
    """
    根據 DIFY_TAG 尋找 /dify-version/{DIFY_TAG}/ 的所有 YMAL 檔案，並將內容嵌入 YAML payload 中。
    """
    try:
        yaml_file = None
        tools_file = []
        agents_file = None
        yaml_dir = os.path.join(os.getcwd(), 'dify-version', DIFY_TAG)

        for filename in os.listdir(yaml_dir):
            if filename.endswith(('.yml', '.yaml')):
                yaml_file = os.path.join(yaml_dir, filename)
                with open(yaml_file, "r", encoding="utf-8") as f:
                    yaml_content = f.read()
                    yaml_content = re.sub("http://140.118.162.94:5678", N8N_BASE_URL, yaml_content, flags=re.IGNORECASE)
                    yaml_content = re.sub("http://140.118.162.94:30000/api/v2/", f"http://{HTTP_DIFY_HOST}:30000/api/v2/", yaml_content, flags=re.IGNORECASE)
                
                logging.info(f"✨ 識別 YAML 檔案：{filename}")

                payload = {
                    "mode": "yaml-content",
                    "yaml_content": yaml_content
                }

                if 'agent' in filename.lower():
                    agents_file = payload
                    logging.info(f"✨ 識別主要 YAML 檔案：{filename}")
                else:
                    tools_file.append(payload)

        logging.info("✅ 獲取 yaml 成功")
        return {
            "tools_file": tools_file,
            "agents_file": agents_file
        }

    except Exception as e:
        log_error(f"獲取 yaml 錯誤：{e}")

def dify_create_workflow(payload, token) -> str:
    """
    發送創建 workflow 的請求，回傳 app_id
    """
    try:
        tools_file = payload["tools_file"]
        agents_file = payload["agents_file"]

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }

        for content in tools_file:
            response = requests.post(DIFY_IMPORT_URL, json=content, headers=headers)

            if response.status_code == 200:
                logging.info("✅ 創建 workflow 成功")

        response = requests.post(DIFY_IMPORT_URL, json=agents_file, headers=headers)

        if response.status_code == 200:
            logging.info("✅ 創建 workflow 成功")
            data = response.json()
            return data.get("app_id")
        else:
            log_error("⚠️ 創建 workflow 失敗")

    except Exception as e:
        log_error(f"創建 workflow 錯誤：{e}")

def get_workflow_token(app_id, token) -> str:
    """
    透過 app_id 拿到 workflow 的 token
    """
    url = f"{API_KEY_BASE}/{app_id}/api-keys"
    headers = {
        "Authorization": f"Bearer {token}"
    }

    try:
        response = requests.post(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        logging.info("✅ 取得 workflow token 成功")
        return data["token"]
    except Exception as e:
        log_error(f"取得 workflow token 錯誤：{e}")

def update_backend_api_key_base(new_api_key_base):
    try:
        env_file = os.path.join(os.getcwd(), 'Backend', '.env')

        if not os.path.exists(env_file):
            raise FileNotFoundError(f".env 檔案不存在於：{env_file}")
        
        # 確保 .env 檔案結尾有換行符號
        with open(env_file, 'a+', encoding='utf-8') as f:
            f.seek(0, os.SEEK_END)   # 到檔案最後
            f.seek(f.tell() - 1, os.SEEK_SET)  # 移到最後一個字元
            last_char = f.read()
            if last_char != '\n':
                f.write('\n')

        # 更新或新增 DIFY_API_KEY
        set_key(env_file, "DIFY_API_KEY", new_api_key_base)
        logging.info(f"✅ 更新 DIFY_API_KEY 成功")

    except Exception as e:
        log_error(f"更新 DIFY_API_KEY 錯誤：{e}")

def add_model_vendor(payload, token):
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
        response = requests.post(DIFY_ADD_MODELS_VENDOR, json=payload, headers=headers)

        if response.status_code == 200:
            logging.info("✅ 安裝模型供應商成功")
        else:
            log_error("⚠️ 安裝模型供應商失敗")

    except Exception as e:
        log_error(f"安裝模型供應商錯誤：{e}")

def set_openai_api_key(token) -> bool:
    try:
        payload = {
            "config_from":"predefined-model",
            "credentials":{"openai_api_key":OPENAI_API_KEY},
            "load_balancing":{"enabled":"false","configs":[]}
        }
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }

        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                response = requests.post(DIFY_SET_OPENAI_API_KEY, json=payload, headers=headers)
                if response.status_code == 201:
                    logging.info("✅ 設定 OPENAI API KEY 成功")
                    return True
                
                time.sleep(1)
            except Exception as e:
                log_error(f"設定 OPENAI API KEY 失敗：{e}")
        log_error("⚠️ 設定 OPENAI API KEY 失敗")

    except Exception as e:
        log_error(f"設定 OPENAI API KEY 錯誤：{e}")

def publish(token):
    try:
        payload = {"marked_name": "", "marked_comment": ""}
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }

        url = f"{DIFY_BASE}/apps/{APP_ID}/workflows/publish"
        
        try:
            response = requests.post(url, json=payload, headers=headers)
            if response.status_code == 201:
                logging.info("✅ 發布 dify 工作流成功")
        except Exception as e:
            log_error(f"發布 dify 工作流失敗：{e}")

    except Exception as e:
        log_error(f"發布 dify 工作流錯誤：{e}")

# ────────────────── 主要步驟封裝成函式 ──────────────────
def step_n8n():
    def step_n8n_setup_container():
        run_shell_script("remove_n8n.sh")
        run_shell_script("run_n8n.sh")
        wait_for_container_ready(["n8n"], timeout=50, require_healthy=False)

    def step_n8n_get_api_key():
        n8n_setup_owner()
        session = n8n_login()
        global N8N_API_KEY
        N8N_API_KEY = n8n_get_api_key(session)

    def step_n8n_init_workflow():
        payloads = json_to_payload()
        n8n_create_workflow(payloads)

    with step_timer("step_n8n_setup_container"):
        _run_with_retry(step_n8n_setup_container)

    with step_timer("step_n8n_get_api_key"):
        _run_with_retry(step_n8n_get_api_key)

    with step_timer("step_n8n_init_workflow"):
        _run_with_retry(step_n8n_init_workflow)

def step_dify():
    def step_dify_setup_container():
        run_shell_script("remove_dify.sh")
        run_shell_script("run_dify.sh")
        wait_for_container_ready(DIFY_CONTAINERS, timeout=50, require_healthy=False)
        wait_for_container_ready(DIFY_CONTAINERS_HEALTHY, timeout=300, require_healthy=True)
    
    def step_dify_setup_owner():
        dify_setup_owner()
        global DIFY_TOKEN
        DIFY_TOKEN = dify_login_and_get_token()

    def step_dify_init_workflow():
        dify_payload = yaml_to_payload()
        global APP_ID
        APP_ID = dify_create_workflow(dify_payload, DIFY_TOKEN)
        workflow_token = get_workflow_token(APP_ID, DIFY_TOKEN)
        update_backend_api_key_base(workflow_token)
        
    def step_dify_init_db():
        payload = {
            "plugin_unique_identifiers": [
                "langgenius/openai:0.0.26@c1e643ac6a7732f6333a783320b4d3026fa5e31d8e7026375b98d44418d33f26"
            ]
        }
        try:
            add_model_vendor(payload, DIFY_TOKEN)
        except Exception as e:
            logging.warning(f"⚠️ add_model_vendor 失敗，跳過 DB 初始化：{e}")
            return            # 直接結束本函式，主程式照常執行
        with step_timer("dify_set_openai_api_key"):
            _run_with_retry(set_openai_api_key, DIFY_TOKEN)
        publish(DIFY_TOKEN)
        # 2025/06/13 移除 "創建知識庫" 功能，包含 upload_file(), init_db() 

    with step_timer("dify_setup_container"):
        _run_with_retry(step_dify_setup_container)

    with step_timer("dify_setup_owner"):
        _run_with_retry(step_dify_setup_owner)

    with step_timer("dify_init_workflow"):
        _run_with_retry(step_dify_init_workflow)

    with step_timer("dify_init_db"):
        step_dify_init_db()

def step_backend():
    with step_timer("backend_init"):
        run_shell_script("run_backend.sh")
        wait_for_container_ready(BACKEND_CONTAINERS, timeout=50, require_healthy=False)
        
def step_dashboard():
    with step_timer("dashboard_init"):
        run_shell_script("run_dashboard.sh")
        wait_for_container_ready(["itri-intent-dashboard"], timeout=50, require_healthy=False)


# ────────────────── 主程式 ──────────────────
if __name__ == "__main__":
    if N8N_EXIST == "NO":
        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = [pool.submit(step_dify), pool.submit(step_n8n)]
            for f in as_completed(futs): f.result()
    elif N8N_EXIST == "YES":
        pass
    else:
        log_error("⚠️ 請設定 .env N8N_EXIST 為 YES/NO")

    ensure_docker_network()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(step_backend), pool.submit(step_dashboard)]
        for f in as_completed(futs): f.result()
        
    logging.info("🎉 部屬全部成功！")