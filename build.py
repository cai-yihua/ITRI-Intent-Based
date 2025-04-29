"""
------------------------------------------
1.  時間用量日誌   →  ./log_time/YYYYMMDD_HHMMSS.log
2.  失敗／錯誤日誌 →  ./error_def/YYYYMMDD_HHMMSS.log
3.  每個主要步驟包成函式，放到 STEPS list 依序執行
4.  執行結束後，讀取 ./error_def/*.log 內最後一份檔，
    若偵測到失敗步驟，立即 **只重跑那幾個步驟** 一次
------------------------------------------
"""
from __future__ import annotations
import os, sys, json, time, subprocess, requests
from pathlib import Path
from datetime import datetime
import logging
from typing import Callable, List, Tuple
from contextlib import contextmanager
from dotenv import load_dotenv, set_key

# ────────────────── 日誌路徑與檔名 ──────────────────
_NOW_STR = datetime.now().strftime("%Y%m%d_%H%M%S")
PATH_TIME   = Path("log_time")
PATH_ERROR  = Path("log_error")
PATH_TIME.mkdir(exist_ok=True)
FILE_TIME  = PATH_TIME  / f"time_{_NOW_STR}.log"
ERROR_FH = None

# ────────────────── Logging 設定 ──────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s| %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(FILE_TIME, encoding="utf-8"),  # 時間／流程
        logging.StreamHandler(sys.stdout)
    ],
)

def log_error(msg: str):
    global ERROR_FH
    logging.error(msg)

    if ERROR_FH is None:
        PATH_ERROR.mkdir(exist_ok=True)
        file_path = PATH_ERROR / f"error_{_NOW_STR}.log"
        ERROR_FH = file_path.open("a", encoding="utf-8")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ERROR_FH.write(f"{timestamp} | ERROR | {msg}\n")
    ERROR_FH.flush()
    raise RuntimeError(msg)

# ────────────────── 共用工具 ──────────────────
dotenv_path = os.path.abspath("./Backend/.env")
load_dotenv(dotenv_path=dotenv_path, override=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

dotenv_path = os.path.abspath(".env")
load_dotenv(dotenv_path=dotenv_path, override=True)  # 明確覆蓋已存在的值

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

@contextmanager
def step_timer(label: str):
    """
    自動把花費時間寫進 ./log_time/… 內
    """
    logging.info(f"▶️ {label} - 開始")
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        logging.info(f"✅ {label} - 結束，耗時 {elapsed:,.2f}s")

def run_shell_script(script_name):
    try:
        # 給予執行權限（等同於 chmod +x）
        subprocess.run(["chmod", "+x", script_name], check=True)

        # 執行腳本
        subprocess.run(["./" + script_name], check=True, text=True)
        logging.info(f"✅ {script_name} 執行成功")
    except Exception as e:
        log_error(f"❗ 未預期錯誤：{e}")

def wait_for_container_ready(url, timeout=50):
    logging.info("⏳ 等待 container 啟動中...")
    for i in range(timeout):
        try:
            response = requests.get(url)
            if response.status_code == 200:
                logging.info("✅ container 已就緒")
                time.sleep(5)
                return True
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(1)
    log_error("❌ container 未在預期時間內就緒")


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
        log_error(f"註冊失敗：{e}")

def dify_login_and_get_token():
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
            logging.info("✅ 成功取得 access token")
            return token
        else:
            log_error("❌ 登入失敗")

    except Exception as e:
        log_error(f"登入失敗：{e}")

def yaml_to_payload():
    """
    根據 DIFY_TAG 尋找 /n8n-version/{DIFY_TAG}/ 的所有 YMAL 檔案，並將內容嵌入 YAML payload 中。
    """
    try:
        yaml_file = None
        yaml_dir = os.path.join(os.getcwd(), 'dify-version', DIFY_TAG)

        for filename in os.listdir(yaml_dir):
            if filename.endswith(('.yml', '.yaml')):
                yaml_file = os.path.join(yaml_dir, filename)
                with open(yaml_file, "r", encoding="utf-8") as f:
                    yaml_content = f.read()

        payload = {
            "mode": "yaml-content",
            "yaml_content": yaml_content
        }

        return payload

    except Exception as e:
        log_error(f"讀取 YAML 發生錯誤：{e}")

def dify_create_workflow(payload, token):
    """
    發送創建 workflow 的請求，回傳 app_id
    """
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
        response = requests.post(DIFY_IMPORT_URL, json=payload, headers=headers)

        if response.status_code == 200:
            logging.info("✅ 成功創建")
            data = response.json()
            return data.get("app_id")
        else:
            log_error("⚠️ 建立 workflow 失敗")

    except Exception as e:
        log_error(f"發送 workflow 請求錯誤：{e}")

def get_workflow_token(app_id, token):
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
        logging.info("✅ 成功取得 Workflow Token")
        return data["token"]
    except Exception as e:
        log_error(f"取得 workflow token 發生錯誤：{e}")

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
        logging.info(f"✅ 成功更新 DIFY_API_KEY")

    except Exception as e:
        log_error(f"更新 .env 檔案失敗：{e}")

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

def set_openai_api_key(token):
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
                    logging.info("✅ 設定 OPENAI 成功")
                    return True
                
                time.sleep(1)
            except Exception as e:
                log_error(f"設定 OPENAI 失敗：{e}")
        log_error("⚠️ 設定 OPENAI 失敗")

    except Exception as e:
        log_error(f"設定 OPENAI 錯誤：{e}")

def upload_file(token):
    try:
        vector_dir = Path(f"dify-version/{DIFY_TAG}/vectorDB")
        if not vector_dir.exists():
            raise FileNotFoundError(vector_dir)

        txt_files = sorted(vector_dir.glob("*.txt"))
        if not txt_files:
            log_error("⚠️ 找不到任何 .txt")

        uploaded_ids = []
        headers = {
            "Authorization": f"Bearer {token}"
        }

        for fp in txt_files:
            try:
                with fp.open("rb") as f:
                    files = {"file": (fp.stem, f, "text/plain")}
                    response = requests.post(DIFY_UPLOAD_TXT, files=files, headers=headers)

                if response.status_code == 201:
                    logging.info("✅ 上傳 file 成功")
                    file_id = response.json()["id"]
                    uploaded_ids.append(file_id)
                else:
                    log_error("⚠️ 上傳 file 失敗")

            except Exception as e:
                log_error(f"上傳 file 錯誤：{e}")
        return uploaded_ids

    except Exception as e:
        log_error(f"上傳 file 錯誤：{e}")

def init_db(file_ids, token):
    try:
        payload = {
            "data_source":{
                "type":"upload_file",
                "info_list":{
                    "data_source_type":"upload_file",
                    "file_info_list":{"file_ids":file_ids}
                }
            },
            "indexing_technique":"high_quality",
            "process_rule":{
                "rules":{
                    "pre_processing_rules":[{
                        "id":"remove_extra_spaces","enabled":"true"},
                        {"id":"remove_urls_emails","enabled":"false"}
                    ],
                    "segmentation":{"separator":"\n\n","max_tokens":4000,"chunk_overlap":50}
                },
                "mode":"custom"},
                "doc_form":"text_model",
                "doc_language":"English",
                "retrieval_model":{
                    "search_method":"semantic_search",
                    "reranking_enable":"false",
                    "reranking_model":{"reranking_provider_name":"","reranking_model_name":""},
                    "top_k":1,"score_threshold_enabled":"false",
                    "score_threshold":0.5,"reranking_mode":"weighted_score",
                    "weights":{
                        "weight_type":"customized",
                        "vector_setting":{
                            "vector_weight":0.7,
                            "embedding_provider_name":"","embedding_model_name":""
                        },
                        "keyword_setting":{"keyword_weight":0.3}
                    }
                },
            "embedding_model":"text-embedding-3-small",
            "embedding_model_provider":"langgenius/openai/openai"
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
        response = requests.post(DIFY_INIT_DB, json=payload, headers=headers)

        if response.status_code != 200:
            log_error("⚠️ 設定資料庫失敗")
        
        ds_id  = response.json()["dataset"]["id"]
        logging.info("✅ 設定資料庫成功")

        # ---------- rename ----------
        rename_body = {"name": DIFY_TAG}
        rename_url  = f"{DIFY_BASE}/datasets/{ds_id}"
        rp = requests.patch(rename_url, json=rename_body, headers=headers)

        if rp.status_code == 200:
            logging.info(f"✅ 名稱已改為 {DIFY_TAG}")
        else:
            log_error("⚠️ 資料庫改名失敗")

        return ds_id

    except Exception as e:
        log_error(f"設定資料庫錯誤：{e}")

# ────────────────── 主要步驟封裝成函式 ──────────────────
def step_remove_old_containers():
    run_shell_script("remove_dify.sh")

def step_start_dify():
    run_shell_script("run_dify.sh")
    wait_for_container_ready(DIFY_SETUP_URL)

def step_dify_owner_login():
    dify_setup_owner()
    global DIFY_TOKEN
    DIFY_TOKEN = dify_login_and_get_token()
    if not DIFY_TOKEN:
        raise RuntimeError("dify login 失敗")

def step_dify_install_vendor_and_app():
    payload = {
        "plugin_unique_identifiers": [
            "langgenius/openai:0.0.19@6b2b2e115b1b9d34a63eb26fadcc33d74330fd2ec06071bb30b8a24b1fab107a"
        ]
    }
    add_model_vendor(payload, DIFY_TOKEN)
    dify_payload = yaml_to_payload()
    app_id = dify_create_workflow(dify_payload, DIFY_TOKEN)
    workflow_token = get_workflow_token(app_id, DIFY_TOKEN)
    update_backend_api_key_base(workflow_token)

def step_dify_upload_vector():
    global FILE_IDS
    FILE_IDS = upload_file(DIFY_TOKEN)
    if FILE_IDS is None:
        raise RuntimeError("上傳向量檔失敗")

def step_build_dashboard():
    run_shell_script("run_dashboard.sh")

def step_build_backend():
    run_shell_script("run_backend.sh")

def step_dify_set_openai_and_init_db():
    set_openai_api_key(DIFY_TOKEN)
    init_db(FILE_IDS, DIFY_TOKEN)

# ────────────────── 步驟清單 & 控制迴圈 ──────────────────
# Tuple(顯示名稱, 對應函式)
STEPS: List[Tuple[str, Callable[[], None]]] = [
    ("刪除舊容器",               step_remove_old_containers),
    ("啟動 Dify",               step_start_dify),
    ("Dify 註冊 / 登入",        step_dify_owner_login),
    ("Dify 安裝模型與建 App",   step_dify_install_vendor_and_app),
    ("Dify 上傳向量檔",         step_dify_upload_vector),
    ("建置 Dashboard",         step_build_dashboard),
    ("建置 Backend",           step_build_backend),
    ("Dify 設定 OpenAI / InitDB", step_dify_set_openai_and_init_db),
]

def run_steps(target_steps: List[str] | None = None) -> List[str]:
    """
    執行所有（或指定）步驟；回傳失敗步驟名稱 list
    """
    failed = []
    for name, fn in STEPS:
        if target_steps and name not in target_steps:
            continue
        try:
            with step_timer(name):
                fn()
        except Exception as e:
            failed.append(name)
    return failed

# ────────────────── 主程式 ──────────────────
if __name__ == "__main__":
    # 初次跑全部
    failed_steps = run_steps()

    # 若有失敗 → 只重跑失敗的步驟一次
    if failed_steps:
        logging.info(f"⚠️ 第一次執行失敗步驟：{failed_steps}，嘗試重跑一次…")
        retry_failed = run_steps(failed_steps)

        if retry_failed:
            logging.error("❌ 仍有步驟失敗，請查看 ./log_error/ 下最新檔案")
        else:
            logging.info("✅ 重跑成功，全部完成！")
    else:
        logging.info("🎉 部屬全部成功！")
