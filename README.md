# ITRI-Intent-Base
## 拉取專案
```bash
git clone --recurse-submodules https://github.com/cai-yihua/ITRI-Intent-Based.git
```

## 本地環境部屬
### Backend
1. 安裝 uv
    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source $HOME/.local/bin/env
    ```
2. 建立虛擬環境
    ```bash
    cd ..
    uv venv
    ```
3. 啟動虛擬環境
    ```bash
    source .venv/bin/activate
    ```
4. 安裝依賴套件
    ```bash
    uv pip install -r ./Backend/requirements/base.txt
    ```

## docker 環境部屬
### 安裝步驟
部屬順序: n8n, dify, Dashboard, Backend
1. 新增 ```.env```
2. 新增 ```Dashboard/.env```
3. 新增 ```Backend/.env```
4. 修改 ```Backend/Dockerfile```
    ```bash
    FROM python:3.9

    WORKDIR /app

    COPY . .

    RUN pip install --no-cache-dir -r requirements/base.txt

    CMD ["sh", "-c", "python manage.py migrate && python manage.py runserver 0.0.0.0:30000"]
    ```
5. 執行佈署腳本
    ```bash
    uv run python build.py
    ```

### 問題解決
1. Sandbox 設定問題
    
    ![alt text](Sandbox-setting.png)