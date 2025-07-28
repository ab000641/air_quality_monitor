# config.py
import os

class Config:
    # 從環境變數獲取資料庫相關設定
    DB_NAME = os.getenv('DB_NAME')
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')

    # 明確設定 SQLALCHEMY_DATABASE_URI
    # 這裡確保即使 .env 沒載入 DATABASE_URL，也有一個備用值
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL')

    # *** 新增除錯語句 ***
    print(f"DEBUG: SQLALCHEMY_DATABASE_URI in Config: {SQLALCHEMY_DATABASE_URI}")
    # *******************

    SQLALCHEMY_TRACK_MODIFICATIONS = False # 關閉 Flask-SQLAlchemy 事件追蹤，減少記憶體消耗

    # 環保署 AQI API Key
    EPA_AQI_API_KEY = os.getenv('EPA_AQI_API_KEY')

    # 環保署 AQI 監測站基本資料 API 端點
    EPA_STATIONS_API_URL = "https://data.moenv.gov.tw/api/v2/aqx_p_07?api_key={api_key}&limit=1000&format=json"

    # 環保署 AQI 即時數據 API 端點
    EPA_AQI_REALTIME_API_URL = "https://data.moenv.gov.tw/api/v2/aqx_p_432?api_key={api_key}&limit=1000&format=json"

    # APScheduler 設定 (排程器)
    SCHEDULER_API_ENABLED = True
    SCHEDULER_JOB_DEFAULTS = {
        'coalesce': False,
        'max_instances': 1
    }
    SCHEDULER_EXECUTORS = {
        'default': {'type': 'threadpool', 'max_workers': 20}
    }
