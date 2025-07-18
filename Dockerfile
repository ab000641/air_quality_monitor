# 使用官方 Python 映像作為基礎映像
FROM python:3.9-slim-bullseye

# 設定工作目錄
WORKDIR /app

# 將 requirements.txt 複製到工作目錄
COPY requirements.txt .

# 安裝 Python 依賴
RUN pip install --no-cache-dir -r requirements.txt

# --- Node.js 和 npm 的安裝 ---
# 第1步：更新 apt 並安裝必要工具
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    apt-transport-https \
    ca-certificates \
    software-properties-common \
    # 這裡可以移除 rm -rf /var/lib/apt/lists/* 因為 NodeSource 腳本會再次更新 apt
    && rm -rf /var/lib/apt/lists/*

# 第2步：使用 NodeSource 官方腳本來添加倉庫和安裝 Node.js
# 這一步會處理 GPG 金鑰、添加倉庫、並安裝 Node.js
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/* # 再次清理 APT 緩存

# 將應用程式的所有檔案複製到工作目錄
COPY . .

# 確保 static/dist 目錄存在，以便 Tailwind 可以寫入輸出檔案
RUN mkdir -p static/dist

# --- 安裝 Tailwind CSS 相關依賴（本地安裝）---
# 注意：這裡不再使用 --global
RUN npm install postcss-cli autoprefixer tailwindcss

# --- 編譯 Tailwind CSS ---
# 確保 Tailwind 的配置文件在 COPY . . 之後才執行
# 現在 npx 會在本地 node_modules/.bin 中查找
RUN npx tailwindcss -i ./static/src/input.css -o ./static/dist/output.css --minify


# 定義應用程式監聽的端口
EXPOSE 5001

# 執行應用程式 (這個將被 docker-compose.yml 中的 command 覆蓋)
# CMD ["python3", "app.py"]
