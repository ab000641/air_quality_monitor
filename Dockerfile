# 使用官方 Python 映像作為基礎映像
FROM python:3.9-slim-buster

# 設定工作目錄
WORKDIR /app

# 將 requirements.txt 複製到工作目錄
COPY requirements.txt .

# 安裝 Python 依賴
RUN pip install --no-cache-dir -r requirements.txt

# --- 新增 Node.js 和 npm 的安裝 ---
# 安裝 Node.js LTS 版本
RUN curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# 安裝 Tailwind CSS 相關依賴
RUN npm install -g postcss-cli autoprefixer tailwindcss

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
