# config.py

import os
from dotenv import load_dotenv

# 從 .env 檔案載入環境變數
# 確保這裡可以找到 .env 檔案，如果 config.py 不在專案根目錄，可能需要指定 dotenv_path
load_dotenv() 

class Config:
    # 從環境變數獲取資料庫相關設定
    DB_NAME = os.getenv('DB_NAME')
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')

    # 明確設定 SQLALCHEMY_DATABASE_URI
    # 這裡確保即使 .env 沒載入 DATABASE_URL，也有一個備用值
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL') or \
                              "postgresql+psycopg2://ab000641:Ab781178@localhost:5433/air_quality_db" # <--- 這行是關鍵！請確保這裡的用戶名、密碼、端口、資料庫名與您的 Docker Compose 硬編碼內容完全一致！

    # *** 新增除錯語句 ***
    print(f"DEBUG: SQLALCHEMY_DATABASE_URI in Config: {SQLALCHEMY_DATABASE_URI}")
    # *******************

    SQLALCHEMY_TRACK_MODIFICATIONS = False # 關閉 Flask-SQLAlchemy 事件追蹤，減少記憶體消耗

    # 環保署 AQI API Key
    EPA_AQI_API_KEY = os.getenv('EPA_AQI_API_KEY')

    # 環保署 AQI 監測站基本資料 API 端點
    EPA_STATIONS_API_URL = "https://data.moenv.gov.tw/api/v2/aqx_p_07?api_key={api_key}&limit=1000&format=json"

    # 環保署 AQI 即時數據 API 端點
    EPA_AQI_REALTIME_API_URL = "https://data.moenv.gov.tw/api/v2/aqx_p_13?api_key={api_key}&limit=1000&format=json"

    # APScheduler 設定 (排程器)
    SCHEDULER_API_ENABLED = True
    SCHEDULER_JOB_DEFAULTS = {
        'coalesce': False,
        'max_instances': 1
    }
    SCHEDULER_EXECUTORS = {
        'default': {'type': 'threadpool', 'max_workers': 20}
    }
