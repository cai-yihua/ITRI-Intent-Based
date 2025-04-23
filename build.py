import os
import json
import time
import requests
import subprocess
from pathlib import Path
from dotenv import load_dotenv, set_key
from datetime import datetime, timezone


dotenv_path = os.path.abspath("./Backend/.env")
load_dotenv(dotenv_path=dotenv_path, override=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

dotenv_path = os.path.abspath(".env")
load_dotenv(dotenv_path=dotenv_path, override=True)  # 明確覆蓋已存在的值

# n8n
N8N_TAG = os.getenv("N8N_TAG")
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
N8N_API_KEY = os.getenv("N8N_API_KEY")


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

def run_shell_script(script_name):
    try:
        # 給予執行權限（等同於 chmod +x）
        subprocess.run(["chmod", "+x", script_name], check=True)

        # 執行腳本
        result = subprocess.run(["./" + script_name], check=True, text=True)
        print(f"✅ {script_name} 執行成功")

    except subprocess.CalledProcessError as e:
        print(f"❌ 執行 {script_name} 發生錯誤：{e}")
    except Exception as e:
        print(f"❗ 未預期錯誤：{e}")

def wait_for_container_ready(url, timeout=50):
    print("⏳ 等待 container 啟動中...", end="")
    for i in range(timeout):
        try:
            response = requests.get(url)
            if response.status_code == 200:
                print("✅ container 已就緒")
                time.sleep(5)
                return True
        except requests.exceptions.ConnectionError:
            pass
        print(".", end="", flush=True)
        time.sleep(1)
    print("\n❌ container 未在預期時間內就緒")
    return False


# n8n  
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
            print("✅ 註冊成功")
            token  = response.cookies.get("n8n-auth")
            print("🍪 n8n-auth token:", token )
            return token 
        else:
            print("❌ 註冊失敗")
            return None

    except Exception as e:
        print(f"註冊失敗：{e}")
        return None

def n8n_login():
    """
    登入並取得 access_token
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
            print("✅ 登入成功，session 已附帶 cookie")
        else:
            print("❌ 無法取得登入後 token，後續請求將失敗")
            return None

        return session

    except Exception as e:
        print(f"登入失敗：{e}")
        return None
   
def n8n_survey(session):
    """
    設定 owner 資訊
    """
    now_utc_iso = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace("+00:00", "Z")

    payload = {
        "companyType": "personal",
        "personalization_survey_n8n_version": "1.88.0",
        "personalization_survey_submitted_at": now_utc_iso,
        "reportedSource": "google",
        "version": "v4"
    }

    try:
        response = session.post(N8N_SURVEY_URL, json=payload)
        response.raise_for_status()
        result = response.json()

        if result.get("data"):
            print("✅ 填寫問卷成功")
        else:
            print("❌ 填寫問卷失敗")
            return None

    except Exception as e:
        print(f"填寫問卷失敗：{e}")
        return None

def n8n_get_api_key(session):
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
            print("✅ 獲取 API KEY 成功")
            api_key = result["data"].get("rawApiKey")
            print("🔑 rawApiKey:", api_key)
            return api_key
        else:
            print("❌ 獲取 API KEY 失敗")
            return None

    except Exception as e:
        print(f"獲取 API KEY 失敗：{e}")
        return None
 
def update_n8n_api_key(new_api_key):
    try:
        env_file = os.path.join(os.getcwd(), '.env')

        if not os.path.exists(env_file):
            raise FileNotFoundError(f".env 檔案不存在於：{env_file}")
        
        # 確保結尾有換行符號
        with open(env_file, 'a+', encoding='utf-8') as f:
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                f.write('\n')  # 如果檔案是空的
            else:
                f.seek(f.tell() - 1)
                last_char = f.read(1)
                if last_char != '\n':
                    f.write('\n')

        # 更新或新增變數
        set_key(env_file, "N8N_API_KEY", new_api_key)
        print(f"✅ 成功更新 N8N_API_KEY")

    except Exception as e:
        print(f"❌ 更新 .env 檔案失敗：{e}")

def json_to_payload():
    """
    根據 N8N_TAG 尋找 /n8n-version/{N8N_TAG}/ 的所有 JSON 檔案，並將內容嵌入 JSON payload 中。
    """
    try:
        allowed_fields = ["name", "nodes", "connections", "settings", "staticData"]
        json_dir = os.path.join(os.getcwd(), 'n8n-version', N8N_TAG)
        payloads = []

        for filename in os.listdir(json_dir):
            if filename.endswith('.json'):
                file_path = os.path.join(json_dir, filename)
                with open(file_path, 'r', encoding='utf-8') as f:
                    json_content = json.load(f)
                    json_payload = {key: json_content[key] for key in allowed_fields if key in json_content}
                payload = {
                    "mode": "json-content",
                    "json_payload": json_payload
                }
                payloads.append(payload)

        return payloads

    except Exception as e:
        print(f"讀取 JSON 檔案發生錯誤：{e}")
        return None
    
def n8n_create_workflow(payloads):
    """
    發送創建 workflow 的請求，回傳 workflow_id
    """
    try:
        load_dotenv(dotenv_path=dotenv_path, override=True)
        N8N_API_KEY = os.getenv("N8N_API_KEY")

        workflow_ids = []
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-N8N-API-KEY": N8N_API_KEY
        }

        for payload in payloads:
            workflow_content = payload["json_payload"]

            response = requests.post(N8N_API_URL, json=workflow_content, headers=headers)
            print("📡 伺服器回應：", response.status_code)

            if response.status_code == 200:
                print("✅ 創建成功")
                data = response.json()
                workflow_id = data.get("id")
                workflow_ids.append(workflow_id)

                # active workflow
                activate_url = f"{N8N_API_URL}/{workflow_id}/activate"
                activate_response = requests.post(activate_url, headers=headers)
                if activate_response.status_code == 200:
                    print("✅ active 成功")
                else:
                    print("⚠️ active workflow workflow_id = {workflow_id} 失敗")
                    return None
            else:
                print(f"⚠️ 建立 workflow name = {workflow_content.name} 失敗")
                return None
        
        return workflow_ids

    except Exception as e:
        print(f"發送 workflow 請求錯誤：{e}")
        return None


# dify
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
            print("✅ 註冊成功")
        else:
            print("❌ 註冊失敗")
            return None

    except Exception as e:
        print(f"註冊失敗：{e}")
        return None

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
            print("✅ 成功取得 access token")
            return token
        else:
            print("❌ 登入失敗")
            return None

    except Exception as e:
        print(f"登入失敗：{e}")
        return None

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
        print(f"讀取 YAML 發生錯誤：{e}")
        return None

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
        print("📡 伺服器回應：", response.status_code)

        if response.status_code == 200:
            print("✅ 成功創建")
            data = response.json()
            return data.get("app_id")
        else:
            print("⚠️ 建立 workflow 失敗")
            return None

    except Exception as e:
        print(f"發送 workflow 請求錯誤：{e}")
        return None

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
        print("🔑 Workflow Token:", data["token"])
        return data["token"]
    except Exception as e:
        print(f"取得 workflow token 發生錯誤：{e}")
        return None

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

        print(f"✅ 成功更新 DIFY_API_KEY")

    except Exception as e:
        print(f"更新 .env 檔案失敗：{e}")

def add_model_vendor(payload, token):
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
        response = requests.post(DIFY_ADD_MODELS_VENDOR, json=payload, headers=headers)
        print("📡 伺服器回應：", response.status_code)

        if response.status_code == 200:
            print("✅ 安裝模型供應商成功")
        else:
            print("⚠️ 安裝模型供應商失敗")

    except Exception as e:
        print(f"安裝模型供應商錯誤：{e}")

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
        response = requests.post(DIFY_SET_OPENAI_API_KEY, json=payload, headers=headers)
        print("📡 伺服器回應：", response.status_code)

        if response.status_code == 201:
            print("✅ 設定 OPENAI 成功")
        else:
            print("⚠️ 設定 OPENAI 失敗")

    except Exception as e:
        print(f"設定 OPENAI 錯誤：{e}")

def upload_file(token):
    try:
        vector_dir = Path(f"dify-version/{DIFY_TAG}/vectorDB")
        if not vector_dir.exists():
            raise FileNotFoundError(vector_dir)

        txt_files = sorted(vector_dir.glob("*.txt"))
        if not txt_files:
            print("⚠️  找不到任何 .txt")
            return []

        uploaded_ids = []
        headers = {
            "Authorization": f"Bearer {token}"
        }

        for fp in txt_files:
            try:
                with fp.open("rb") as f:
                    files = {"file": (fp.stem, f, "text/plain")}
                    response = requests.post(DIFY_UPLOAD_TXT, files=files, headers=headers)
                print("📡 伺服器回應：", response.status_code)

                if response.status_code == 201:
                    print("✅ 上傳 file 成功")
                    file_id = response.json()["id"]
                    uploaded_ids.append(file_id)
                else:
                    print("⚠️ 上傳 file 失敗")

            except Exception as e:
                print(f"上傳 file 錯誤：{e}")
        return uploaded_ids

    except Exception as e:
        print(f"上傳 file 錯誤：{e}")

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
        print("📡 伺服器回應：", response.status_code)

        if response.status_code != 200:
            print("⚠️ 設定資料庫失敗")
            return None
        
        ds_id  = response.json()["dataset"]["id"]
        print("✅ 設定資料庫成功 id =", ds_id)

        # ---------- rename ----------
        rename_body = {"name": DIFY_TAG}
        rename_url  = f"{DIFY_BASE}/datasets/{ds_id}"
        rp = requests.patch(rename_url, json=rename_body, headers=headers)
        print("📡 rename →", rp.status_code)

        if rp.status_code == 200:
            print("✅ 名稱已改為", DIFY_TAG)
        else:
            print("⚠️ 改名失敗：", rp.text)

        return ds_id

    except Exception as e:
        print(f"設定資料庫錯誤：{e}")


if __name__ == "__main__":
    # 刪除舊容器
    run_shell_script("remove_n8n.sh")
    run_shell_script("remove_dify.sh")

    # input("輸入任一鍵以繼續")
    time.sleep(5)

    #################### n8n 佈署 ####################
    # 1) 啟動 n8n container 並等待服務可用
    run_shell_script("run_n8n.sh")
    wait_for_container_ready(N8N_BASE_URL)

    # 2) 註冊 owner
    token = n8n_setup_owner()

    # 3) 登入
    session = n8n_login()
    if session:
        # 4) 填寫調查問卷
        n8n_survey(session)

        # 5) 獲取 API_KEY
        api_key = n8n_get_api_key(session)

    # 6) 更新 .env N8N_API_KEY
    update_n8n_api_key(api_key)

    # 7) 讀取 tag 對應的 n8n JSON(s)
    n8n_payloads = json_to_payload()
    
    # 8) 創建 n8n workflow(s) 並 active
    resp = n8n_create_workflow(n8n_payloads)


    #################### dify 佈署 ####################
    # 1) 啟動 dify container
    run_shell_script("run_dify.sh")
    wait_for_container_ready(DIFY_SETUP_URL)

    # 2) 註冊 owner
    dify_setup_owner()

    # 3) 登入並取得 token
    dify_token = dify_login_and_get_token()

    # 4) 讀取 tag 對應的 dify YAML
    dify_payload = yaml_to_payload()

    # 5) 創建 dify workflow
    app_id = dify_create_workflow(dify_payload, dify_token)

    # 6) 取得 workflow token
    dify_workflow_token = get_workflow_token(app_id, dify_token)

    # 7) 更新 Backend .env DIFY_API_KEY
    update_backend_api_key_base(dify_workflow_token)

    # 8) 安裝 OPENAI 模型供應商，並等安裝完成
    payload = {"plugin_unique_identifiers":["langgenius/openai:0.0.19@6b2b2e115b1b9d34a63eb26fadcc33d74330fd2ec06071bb30b8a24b1fab107a"]}
    add_model_vendor(payload, dify_token)
    time.sleep(10)

    # 9) 設定 OPENAI API_KEY
    set_openai_api_key(dify_token)

    # 10) 上傳 .txt
    file_ids = upload_file(dify_token)
    
    # 11) 設定知識庫
    dataset_id = init_db(file_ids, dify_token)


    ################## Dashbroad 佈署 #################
    # 1) 啟動 Dashbroad
    run_shell_script("run_dashboard.sh")


    ################## Backend 佈署 ###################
    # 1) 啟動 Backend container
    run_shell_script("run_backend.sh")