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
    && rm -rf /var/lib/apt/lists/*

# 第2步：添加 NodeSource GPG 金鑰
# 使用 NodeSource 官方推薦的安裝方式來確保金鑰正確添加
RUN curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor | tee /etc/apt/trusted.gpg.d/nodesource.gpg >/dev/null

# 第3步：添加 NodeSource 倉庫到 APT sources.list.d
# 注意：這裡我們使用 /etc/apt/trusted.gpg.d/nodesource.gpg 作為 signed-by
RUN echo "deb [signed-by=/etc/apt/trusted.gpg.d/nodesource.gpg] https://deb.nodesource.com/node_20.x bullseye main" | tee /etc/apt/sources.list.d/nodesource.list

# 第4步：更新 apt 倉庫列表並安裝 nodejs
RUN apt-get update && apt-get install -y nodejs && rm -rf /var/lib/apt/lists/*

# 安裝 Tailwind CSS 相關依賴
# 注意：這裡使用 npm install --global 是為了確保在任何地方都能執行 npx tailwindcss
RUN npm install --global postcss-cli autoprefixer tailwindcss

# 將應用程式的所有檔案複製到工作目錄
COPY . .

# 確保 static/dist 目錄存在，以便 Tailwind 可以寫入輸出檔案
RUN mkdir -p static/dist

# --- 編譯 Tailwind CSS ---
# 確保 Tailwind 的配置文件在 COPY . . 之後才執行
RUN npx tailwindcss -i ./static/src/input.css -o ./static/dist/output.css --minify

# 定義應用程式監聽的端口
EXPOSE 5001

# 執行應用程式 (這個將被 docker-compose.yml 中的 command 覆蓋)
# CMD ["python3", "app.py"]
