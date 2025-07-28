# 使用官方 Python 3.9 輕量級映像 (保持與之前成功的環境一致性)
FROM python:3.9-slim-bookworm

# 設定工作目錄
WORKDIR /app

# 將本地的 requirements.txt 複製到容器中
COPY requirements.txt .

# 安裝 Python 依賴和必要的系統套件
# 這些是運行您的應用程式和進行網路診斷所必需的
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    netcat-openbsd \
    curl \
    dnsutils && \
    pip install --no-cache-dir -r requirements.txt && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 將應用程式的所有檔案複製到工作目錄
COPY . .

# 定義應用程式運行在 5001 埠
EXPOSE 5001

# 定義容器啟動時執行的命令
# 使用 Gunicorn 來運行 Flask 應用程式，建議在生產環境使用
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "app:app"]
