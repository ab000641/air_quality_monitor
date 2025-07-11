import os
from dotenv import load_dotenv

# 從 .env 檔案載入環境變數
load_dotenv()

class Config:
    # 從環境變數獲取資料庫相關設定
    DB_NAME = os.getenv('DB_NAME')
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    # DATABASE_URL 會在 docker-compose.yml 中組裝並傳給容器，或在這裡直接組裝
    # 這裡先使用一個範例，實際在 Flask app.py 中會用 os.getenv('DATABASE_URL')
    # 或者透過 Docker Compose 的方式自動設定
    # SQLALCHEMY_DATABASE_URI = f"postgresql://{DB_USER}:{DB_PASSWORD}@db:5432/{DB_NAME}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False # 關閉 Flask-SQLAlchemy 事件追蹤，減少記憶體消耗

    # 環保署 AQI API Key
    EPA_AQI_API_KEY = os.getenv('EPA_AQI_API_KEY')

    # 環保署 AQI 監測站基本資料 API 端點
    EPA_STATIONS_API_URL = "https://data.moenv.gov.tw/api/v2/aqx_p_97?api_key={api_key}&limit=1000&format=json"

    # 環保署 AQI 即時數據 API 端點
    EPA_AQI_REALTIME_API_URL = "https://data.moenv.gov.tw/api/v2/aqx_p_01?api_key={api_key}&limit=1000&format=json"

    # APScheduler 設定 (排程器)
    SCHEDULER_API_ENABLED = True
    SCHEDULER_JOB_DEFAULTS = {
        'coalesce': False,
        'max_instances': 1
    }
    SCHEDULER_EXECUTORS = {
        'default': {'type': 'threadpool', 'max_workers': 20}
    }