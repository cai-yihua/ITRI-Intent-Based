# ITRI-Intent-Base
## 拉取專案
```bash
git clone --recurse-submodules https://github.com/cai-yihua/ITRI-Intent-Based.git
```

## 本地環境部屬
### n8n

### dify

### Dashbroad
1. 安裝 nvm
    ```bash
    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.3/install.sh | bash
    source ~/.nvm/nvm.sh
    ```
2. 安裝並啟動 Node.js
    ```bash
    nvm install 20.18.0
    nvm use 20.18.0
    ```
3. 安裝依賴套件
    ```bash
    cd User-Dashbroad/
    npm install
    ```

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
