from __future__ import annotations
import os, sys, time, requests, logging
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager
from dotenv import load_dotenv
from typing import List
from tenacity import retry, stop_after_attempt, wait_fixed

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

dotenv_path = os.path.abspath(".env")
load_dotenv(dotenv_path=dotenv_path, override=True)

# dify
DIFY_TAG = os.getenv("DIFY_TAG")
DIFY_EMAIL = os.getenv("DIFY_EMAIL")
DIFY_PASSWORD = os.getenv("DIFY_PASSWORD")
DIFY_LOGIN_URL = os.getenv("DIFY_LOGIN_URL")

DIFY_SET_OPENAI_API_KEY = os.getenv("DIFY_SET_OPENAI_API_KEY")
DIFY_UPLOAD_TXT = os.getenv("DIFY_UPLOAD_TXT")
DIFY_INIT_DB = os.getenv("DIFY_INIT_DB")
DIFY_BASE = os.getenv("DIFY_BASE")


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


# ────────────────── dify ──────────────────
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

def upload_file(token) -> List[str]:
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

def init_db(file_ids, token) -> str:
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
def step_dify():
    def step_dify_init_db():
        global DIFY_TOKEN
        DIFY_TOKEN = dify_login_and_get_token()
        with step_timer("dify_set_openai_api_key"):
            _run_with_retry(set_openai_api_key, DIFY_TOKEN)
        file_ids = upload_file(DIFY_TOKEN)
        init_db(file_ids, DIFY_TOKEN)

    with step_timer("dify_init_db"):
        step_dify_init_db()


# ────────────────── 主程式 ──────────────────
if __name__ == "__main__":
    step_dify()
        
    logging.info("🎉 部屬 dify 知識庫成功！")