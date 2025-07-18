# 使用官方 Python 映像作為基礎映像
FROM python:3.9-slim-bullseye

# 設定工作目錄
WORKDIR /app

# 將 requirements.txt 複製到工作目錄並安裝 Python 依賴
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 將應用程式的所有檔案複製到工作目錄
# 注意：此時 static/dist/output.css 應該已經存在於您 Git 倉庫的本地副本中
COPY . .

# 定義應用程式監聽的端口
EXPOSE 5001

# 執行應用程式 (這個將被 docker-compose.yml 中的 command 覆蓋)
CMD ["python3", "app.py"]
