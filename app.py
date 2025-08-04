import os
import requests
import json
import hmac
import hashlib
import logging # 新增：導入 logging 模組

from flask import Flask, render_template, request, redirect, url_for, flash, session, abort, jsonify
from flask_apscheduler import APScheduler
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, FollowEvent, LocationMessage

from config import Config
from models import db, Station, LineUser, LineUserStationPreference
from utils.distance import calculate_distance
from redis import Redis
from rq import Queue

redis_connection = Redis.from_url(os.getenv('REDIS_URL', 'redis://localhost:6379/0'))
aqi_queue = Queue('aqi_queue', connection=redis_connection) # 確保這裡的佇列名稱是 'aqi_queue'

# 創建 Flask 應用程式實例
app = Flask(__name__)
app.config.from_object(Config)

# 初始化資料庫
db.init_app(app)

# --- 新增：定義台灣縣市的從北到南順序 ---
COUNTY_ORDER = [
    "基隆市", "臺北市", "新北市", "桃園市", "新竹市", "新竹縣", "苗栗縣", "臺中市", "彰化縣", "南投縣",
    "雲林縣", "嘉義市", "嘉義縣", "臺南市", "高雄市", "屏東縣", "宜蘭縣", "花蓮縣", "臺東縣", "澎湖縣",
    "金門縣", "連江縣"
]
# 請確保這裡的縣市名稱與您的 Station 資料庫中儲存的 `county` 欄位的值完全一致。
# 環境部 API 通常使用「臺」字而非「台」字，因此這裡使用「臺北市」、「臺中市」。

# --- 新增：定義台灣縣市所屬的地理區域 ---
COUNTY_TO_REGION = {
    "基隆市": "北", "臺北市": "北", "新北市": "北", "桃園市": "北", "新竹市": "北", "新竹縣": "北", "苗栗縣": "北",
    "臺中市": "中", "彰化縣": "中", "南投縣": "中", "雲林縣": "中",
    "嘉義市": "南", "嘉義縣": "南", "臺南市": "南", "高雄市": "南", "屏東縣": "南",
    "宜蘭縣": "東", "花蓮縣": "東", "臺東縣": "東",
    "澎湖縣": "離島", "金門縣": "離島", "連江縣": "離島" # 可將離島單獨列出或歸入某個大區
}

# --- 定義區域的排序順序 (用於前端按鈕顯示和邏輯判斷) ---
REGION_ORDER = ["北", "中", "南", "東", "離島"] # 您可以調整這個順序

# AQI_STATUS_ORDER 定義
AQI_STATUS_ORDER = [
    "良好",
    "普通",
    "對敏感族群不健康",
    "不健康",
    "非常不健康",
    "危害",
    "維護",
    "無效",
    "N/A",  # 或 "N/A"
    "未知"
]

# 您可能需要一個映射字典，將中文狀態映射到英文或拼音
# 這樣在模板中生成類別時，就可以使用英文/拼音名稱
STATUS_TO_CLASS_NAME = {
    "良好": "good",
    "普通": "moderate",
    "對敏感族群不健康": "unhealthy-for-sensitive",
    "不健康": "unhealthy",
    "非常不健康": "very-unhealthy",
    "危害": "hazardous",
    "維護": "maintenance",
    "無效": "invalid",
    "N/A": "na", # 注意這裡轉為小寫 'na'
    "未知": "unknown"
}


# 設置 Flask session 的密鑰，在生產環境中必須設置並使用複雜的隨機字串
app.secret_key = os.getenv('SECRET_KEY', 'a_very_secret_key_for_dev')

# --- 配置應用程式日誌 ---
# 確保日誌輸出到標準輸出，以便 Docker logs 可以捕獲
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
app.logger.setLevel(logging.INFO) # 設定 Flask 應用程式的日誌級別

# LINE Messaging API 配置
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    app.logger.warning("警告: LINE_CHANNEL_ACCESS_TOKEN 或 LINE_CHANNEL_SECRET 未設定，LINE Bot 功能將受限。")
    # 在生產環境中，這裡應該直接退出或報錯

# 初始化排程器
scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()

# --- 資料庫初始化函式 ---
def init_db():
    with app.app_context():
        db.create_all()
        app.logger.info("--- 資料庫表結構已完成 ---")
        if not Station.query.first():
            app.logger.info("--- 正在首次抓取監測站資料並填充資料庫 ---")
            fetch_and_store_all_stations()
            app.logger.info("--- 監測站資料填充完成 ---")

            # --- 僅在首次填充測站資料後，立即抓取並填充即時 AQI 數據 ---
            app.logger.info("--- 正在首次抓取即時空氣品質數據並填充資料庫 (即時) ---")
            fetch_and_store_realtime_aqi()
            app.logger.info("--- 即時空氣品質數據首次填充完成 ---")
            # -----------------------------------------------------------------
        else:
            app.logger.info("--- 監測站資料已存在，跳過首次抓取與數據填充。排程器將處理後續更新。---")


# --- 定義排程任務 ---
@scheduler.task('cron', id='fetch_aqi_data_job', minute=5, misfire_grace_time=900)
def fetch_aqi_data_job():
    """
    排程任務：抓取即時空氣品質數據，並在抓取完成後觸發個人化通知發送。
    """
    with app.app_context():
        app.logger.info(f"--- 排程任務: 正在抓取即時空氣品質數據 ({datetime.now()}) ---")
        fetch_and_store_realtime_aqi()
        app.logger.info("--- 排程任務: 即時空氣品質數據抓取完成 ---")
        
        # --- 新增：在數據抓取完成後，立即觸發個人化空氣品質通知 (基於用戶位置) ---
        # 注意：這裡直接觸發 send_personalized_aqi_push_job，而不是另外設定一個獨立的 cron。
        # 這樣可以確保推送的是最新數據。但如果這個推送任務很耗時，可能會影響下一次抓取。
        # 更好的做法是讓 send_personalized_aqi_push_job 獨立運行 (例如每小時)。
        # 考慮到數據更新頻率和通知頻率可能不同，我會保留兩個獨立的排程，
        # 但確保 send_personalized_aqi_push_job 運行時能獲取到最新的數據。
        #
        # 因此，維持您原先的設計，讓 `send_personalized_aqi_push_job` 獨立排程更合理，
        # 因為即時數據可能每小時抓取一次，但推送給所有用戶可能需要較長間隔 (避免 API 限制和打擾用戶)。
        #
        # 因此，我建議保留 `send_personalized_aqi_push_job` 作為一個獨立的每小時排程，
        # 讓它在自己的時間點 (例如每小時的第 0 分鐘) 去查詢最新的 AQI 數據。
        # 這樣 `fetch_aqi_data_job` 仍然只負責抓取和儲存，而不會被推送邏輯綁死。
        # 您的原始代碼中，`send_personalized_aqi_alerts()` 被我建議添加到 `fetch_aqi_data_job` 內，
        # 但在綜合考量後，把它變成獨立的 `send_personalized_aqi_push_job` 更具彈性。
        # 所以，我將移除原先在 `fetch_aqi_data_job` 內部直接調用 `send_personalized_aqi_alerts()` 的建議，
        # 轉而建議新增一個獨立的 `send_personalized_aqi_push_job` 定時任務。

        # 這裡不直接調用 send_personalized_aqi_push_job，而是讓它作為一個獨立的排程運行
        # 這樣可以解耦數據抓取和消息推送的頻率
        app.logger.info("--- 排程任務: 即時空氣品質數據抓取完成。個人化通知將由獨立排程處理。---")

@scheduler.task('interval', id='check_and_send_alerts_job', minutes=30, misfire_grace_time=600)
def check_and_send_alerts_job():
    with app.app_context():
        app.logger.info(f"--- 排程任務: 正在檢查空氣品質警報並發送通知 ({datetime.now()}) ---")
        send_aqi_alerts()
        app.logger.info("--- 排程任務: 空氣品質警報檢查完成 ---")

# --- API 互動函式 (此部分保持不變) ---
def fetch_and_store_all_stations():
    """
    從環保署 API 抓取所有監測站列表並儲存到資料庫
    """
    api_url = Config.EPA_STATIONS_API_URL.format(api_key=Config.EPA_AQI_API_KEY)
    app.logger.info(f"嘗試從 API 獲取所有監測站資料。URL: {api_url}")
    try:
        response = requests.get(api_url)
        response.raise_for_status()
        data = response.json()

        if 'records' in data:
            new_stations_count = 0
            updated_stations_count = 0
            for record in data['records']:
                station_name = record.get('sitename')
                site_id = record.get('siteid')
                county = record.get('county')
                latitude = float(record.get('twd97lat')) if record.get('twd97lat') else None
                longitude = float(record.get('twd97lon')) if record.get('twd97lon') else None

                # --- 新增：根據 COUNTY_TO_REGION 獲取區域資訊 ---
                region = COUNTY_TO_REGION.get(county, "未知區域") # 如果縣市不在映射中，則為未知區域

                if site_id and station_name:
                    existing_station = Station.query.filter_by(site_id=site_id).first()
                    if not existing_station:
                        new_station = Station(
                            site_id=site_id,
                            name=station_name,
                            county=county,
                            latitude=latitude,
                            longitude=longitude,
                            region=region # 新增 region 欄位
                        )
                        db.session.add(new_station)
                        new_stations_count += 1
                    else:
                        existing_station.name = station_name
                        existing_station.county = county
                        existing_station.latitude = latitude
                        existing_station.longitude = longitude
                        existing_station.region = region # 更新 region 欄位
                        updated_stations_count += 1
            db.session.commit()
            app.logger.info(f"成功抓取並儲存監測站資料。新增 {new_stations_count} 個，更新 {updated_stations_count} 個。")
        else:
            app.logger.warning("API 返回數據中未找到 'records' 鍵。完整數據: %s", json.dumps(data))

    except requests.exceptions.RequestException as e:
        app.logger.error(f"抓取監測站資料失敗 (RequestException): {e}", exc_info=True)
    except json.JSONDecodeError as e:
        app.logger.error(f"API 返回數據不是有效的 JSON: {e}. 原始響應: {response.text}", exc_info=True)
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"處理監測站資料時發生錯誤: {e}", exc_info=True)


def fetch_and_store_realtime_aqi():
    """
    從環保署 API 抓取即時空氣品質數據並更新到資料庫的 Station 表中
    """
    api_url = Config.EPA_AQI_REALTIME_API_URL.format(api_key=Config.EPA_AQI_API_KEY)
    app.logger.info(f"嘗試從 API 獲取即時空氣品質數據。URL: {api_url}")
    try:
        response = requests.get(api_url)
        response.raise_for_status()
        data = response.json()
        app.logger.info(f"即時 AQI API 返回數據 (部分): {json.dumps(data, indent=2)[:500]}...")

        if 'records' in data:
            updated_count = 0
            for record in data['records']:
                site_id = record.get('siteid')

                # 使用 helper 函式安全地轉換為整數，處理空字串和非數字情況
                def safe_int_conversion(value):
                    try:
                        return int(value) if value else None
                    except ValueError:
                        return None
                    
                aqi = safe_int_conversion(record.get('aqi'))
                status = record.get('status')
                pm25 = safe_int_conversion(record.get('pm2.5'))
                pm10 = safe_int_conversion(record.get('pm10'))
                publish_time_str = record.get('publishtime')
                publish_time = None
                if publish_time_str:
                    try:
                        # API 返回的時間格式是 "YYYY/MM/DD HH:MM:SS"
                        publish_time = datetime.strptime(publish_time_str, '%Y/%m/%d %H:%M:%S') 
                    except ValueError:
                        app.logger.warning(f"無法解析時間格式: {publish_time_str}")

                if site_id:
                    station = Station.query.filter_by(site_id=site_id).first()
                    if station:
                        station.aqi = aqi
                        station.status = status
                        station.pm25 = pm25
                        station.pm10 = pm10
                        station.publish_time = publish_time
                        updated_count += 1
                    else:
                        app.logger.warning(f"未能找到 ID 為 {site_id} 的測站，無法更新即時數據。")
            db.session.commit()
            app.logger.info(f"成功更新 {updated_count} 個測站的即時 AQI 數據。")
        else:
            app.logger.warning("即時 AQI API 返回數據中未找到 'records' 鍵。完整數據: %s", json.dumps(data, indent=2))

    except requests.exceptions.RequestException as e:
        app.logger.error(f"抓取即時 AQI 數據失敗 (RequestException): {e}", exc_info=True)
    except json.JSONDecodeError as e:
        app.logger.error(f"即時 AQI API 返回數據不是有效的 JSON: {e}. 原始響應: {response.text}", exc_info=True)
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"處理即時 AQI 數據時發生錯誤: {e}", exc_info=True)


# --- LINE Messaging API 相關函式 (此部分保持不變) ---

def send_line_message(line_user_id, message_text):
    """
    使用 LINE Messaging API 發送推播訊息給指定用戶
    """
    try:
        line_bot_api.push_message(
            line_user_id,
            TextMessage(text=message_text)
        )
        app.logger.info(f"LINE 訊息發送成功給 {line_user_id}")
        return True
    except Exception as e:
        app.logger.error(f"LINE 訊息發送失敗給 {line_user_id}: {e}", exc_info=True)
        return False

def send_aqi_alerts():
    """
    檢查並發送 AQI 警報 (使用 LINE Messaging API)
    """
    user_station_preferences = LineUserStationPreference.query.all()
    app.logger.info(f"正在檢查 {len(user_station_preferences)} 個用戶的空氣品質警報。")

    for preference in user_station_preferences:
        line_user = preference.line_user
        station = preference.station
        threshold_value = preference.threshold_value

        # 確保 LineUser 活躍且測站存在
        # is_subscribed 欄位很重要，如果用戶封鎖了 Bot，這個欄位應該被設定為 False
        if line_user and line_user.is_subscribed and station:
            can_send_alert = True
            if preference.last_alert_sent_at:
                # 假設每 3 小時才發送一次相同測站的警報，即使 AQI 持續超標
                if datetime.utcnow() - preference.last_alert_sent_at < timedelta(hours=3):
                    can_send_alert = False

            if station.aqi is not None and station.aqi >= threshold_value and can_send_alert:
                message = (
                    f"\n【空氣品質警報】\n"
                    f"測站：{station.county} - {station.name}\n"
                    f"目前 AQI：{station.aqi} ({station.status})\n"
                    f"發布時間：{station.publish_time.strftime('%Y-%m-%d %H:%M') if station.publish_time else 'N/A'}\n"
                    f"已超過您設定的閾值：{threshold_value}！"
                )
                app.logger.info(f"嘗試向 Line 用戶 {line_user.line_user_id} (Station: {station.name}) 發送警報: AQI {station.aqi}")
                
                if send_line_message(line_user.line_user_id, message):
                    preference.last_alert_sent_at = datetime.utcnow()
                    db.session.commit()
                else:
                    # 如果發送失敗，考慮將用戶 is_subscribed 設為 False (例如用戶封鎖了 Bot)
                    # 只有當 LINE API 返回用戶封鎖的錯誤碼時才這樣做，否則會誤判
                    # 這需要更細緻的錯誤處理，這裡簡化
                    app.logger.warning(f"警告: 無法發送 LINE 訊息給用戶 {line_user.line_user_id}。")
            # else:
            #     app.logger.debug(f"測站 {station.name} AQI {station.aqi} 未超過閾值 {threshold_value} 或未到發送間隔。")


# --- 路由 (Routes) ---

@app.route('/')
def index():
    with app.app_context():
        # 修改點 2: 移除每次首頁請求都觸發數據更新的行為，讓排程器專職負責
        # app.logger.info("首頁請求：觸發即時空氣品質數據更新。")
        # fetch_and_store_realtime_aqi() # <-- 移除這行

        # 獲取所有監測站 (不依縣市排序，因為我們要進行自定義排序)
        # 這裡可以根據 Station.name 進行初步排序，以便在同一縣市內有穩定順序
        all_stations = Station.query.order_by(Station.name).all() 
        app.logger.info(f"首頁請求：從資料庫獲取到 {len(all_stations)} 個測站資料。")

        # 關鍵步驟：強制刷新會話中的所有 Station 對象
        for station in all_stations:
            db.session.refresh(station) # 刷新每個 Station 對象的屬性

        # 根據 COUNTY_ORDER 進行自定義排序
        # 如果某個測站的縣市不在 COUNTY_ORDER 列表中，將其排在最後
        def get_county_order_key(station):
            try:
                # 嘗試獲取縣市在 COUNTY_ORDER 中的索引
                return COUNTY_ORDER.index(station.county)
            except ValueError:
                # 如果縣市不在列表中，給它一個很高的索引，讓它排在最後
                return len(COUNTY_ORDER)

        stations = sorted(all_stations, key=get_county_order_key)

        # 檢查是否有任何測站的 AQI 是 None，幫助調試 (保持不變，因為這行很有用)
        for station in stations:
            if station.aqi is None:
                app.logger.warning(f"測站 {station.name} ({station.site_id}) 的 AQI 仍然為 None。")
            else:
                app.logger.info(f"測站 {station.name} ({station.site_id}) AQI: {station.aqi}, Status: {station.status}")


    return render_template('index.html', stations=stations, 
                           aqi_status_order=AQI_STATUS_ORDER, 
                           region_order=REGION_ORDER,
                           status_to_class_name=STATUS_TO_CLASS_NAME)

@app.route('/api/aqi_data')
def aqi_data_api():
    """
    提供即時空氣品質數據的 JSON API 端點。
    數據將根據 COUNTY_ORDER 和 station.name 排序。
    """
    with app.app_context():
        # 如果每小時的排程已經足夠，這裡可不執行 (保持不變，已註解掉)
        # fetch_and_store_realtime_aqi() 

        all_stations = Station.query.order_by(Station.name).all()

        def get_county_order_key(station):
            try:
                return COUNTY_ORDER.index(station.county)
            except ValueError:
                return len(COUNTY_ORDER)

        stations = sorted(all_stations, key=get_county_order_key)

        # 準備要返回的 JSON 數據
        # 為了避免序列化問題和傳輸不必要的數據，只返回前端需要的部分
        data = []
        for station in stations:
            # 確保獲取最新數據，特別是對於 `session.refresh` 後
            db.session.refresh(station) # 確保獲取 Station 對象的最新屬性

            # 獲取對應的英文類別名稱
            status_class_name = STATUS_TO_CLASS_NAME.get(station.status, 'unknown')

            data.append({
                'id': station.id,
                'site_id': station.site_id,
                'name': station.name,
                'county': station.county,
                'region': station.region,
                'aqi': station.aqi,
                'status': station.status,
                'pm25': station.pm25,
                'pm10': station.pm10,
                'publish_time': station.publish_time.strftime('%Y-%m-%d %H:%M') if station.publish_time else 'N/A',
                'status_class_name': status_class_name # 將這個也傳遞給前端，方便生成類別
            })
        
        return jsonify(data) # 使用 Flask 的 jsonify 函數返回 JSON 響應

# *** 手動綁定路由可以作為備用，但主要將透過 Webhook 獲取用戶 ID (此部分保持不變) ***
@app.route('/manual_line_binding', methods=['GET', 'POST'])
def manual_line_binding():
    if request.method == 'POST':
        line_user_id = request.form.get('line_user_id')
        selected_station_ids_str = request.form.get('station_ids') # 逗號分隔的字符串
        threshold_value = request.form.get('threshold', type=int, default=100)

        if not line_user_id:
            flash("LINE 用戶 ID 不可為空。", "error")
            return redirect(url_for('manual_line_binding'))

        if not selected_station_ids_str:
            flash("請選擇至少一個監測站。", "error")
            return redirect(url_for('manual_line_binding'))

        station_ids = [int(sid) for sid in selected_station_ids_str.split(',') if sid.isdigit()]
        if not station_ids:
            flash("無效的監測站選擇。", "error")
            return redirect(url_for('manual_line_binding'))

        with app.app_context():
            try:
                line_user = LineUser.query.filter_by(line_user_id=line_user_id).first()
                if not line_user:
                    line_user = LineUser(line_user_id=line_user_id, default_threshold=threshold_value, is_subscribed=True)
                    db.session.add(line_user)
                    db.session.commit()
                    app.logger.info(f"已新增 LINE 用戶: {line_user_id}")
                else:
                    line_user.default_threshold = threshold_value
                    line_user.is_subscribed = True
                    db.session.commit()
                    app.logger.info(f"已更新 LINE 用戶: {line_user_id}")

                for station_id in station_ids:
                    station = Station.query.get(station_id)
                    if station:
                        existing_preference = LineUserStationPreference.query.filter_by(
                            line_user_id=line_user.line_user_id,
                            station_id=station.id
                        ).first()

                        if not existing_preference:
                            new_preference = LineUserStationPreference(
                                line_user=line_user,
                                station=station,
                                threshold_value=threshold_value,
                                last_alert_sent_at=None
                            )
                            db.session.add(new_preference)
                            app.logger.info(f"綁定測站 {station.name} 到用戶 {line_user.line_user_id}")
                        else:
                            existing_preference.threshold_value = threshold_value
                            app.logger.info(f"更新用戶 {line_user.line_user_id} 在測站 {station.name} 的閾值")
                db.session.commit()
                flash("LINE 訂閱和測站綁定成功！", "success")

            except IntegrityError:
                db.session.rollback()
                flash("綁定失敗：資料庫重複或錯誤。", "error")
                app.logger.error("綁定失敗：資料庫重複或錯誤。", exc_info=True)
            except Exception as e:
                db.session.rollback()
                flash(f"綁定過程中發生錯誤: {e}", "error")
                app.logger.error(f"綁定過程中發生錯誤: {e}", exc_info=True)
        
        return redirect(url_for('index'))
    
    stations = Station.query.order_by(Station.county, Station.name).all()
    return render_template('manual_line_binding.html', stations=stations)


# --- 新增 LINE Webhook 路由 (此部分保持不變) ---
@app.route("/webhook/line", methods=['POST'])
def callback():
    # 獲取 X-Line-Signature 頭部的值
    signature = request.headers['X-Line-Signature']

    # 獲取請求體作為文本
    body = request.get_data(as_text=True)
    app.logger.info(f"收到 LINE Webhook 請求。請求體: {body[:500]}...") # 避免打印過長的請求體

    try:
        # 使用 WebhookHandler 處理請求體和簽名
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.error("簽名驗證失敗。請檢查您的 Channel Secret。")
        abort(400) # 返回 400 Bad Request
    except Exception as e:
        app.logger.error(f"處理 Webhook 事件時發生錯誤: {e}", exc_info=True)
        abort(500) # 返回 500 Internal Server Error

    return 'OK' # 必須返回 'OK' 給 LINE

# --- 定義處理各種 LINE 事件的函式 (此部分保持不變) ---
@handler.add(FollowEvent)
def handle_follow(event):
    # 當用戶將 Bot 加為好友時觸發
    line_user_id = event.source.user_id
    app.logger.info(f"收到 Follow Event 來自用戶: {line_user_id}")
    
    with app.app_context():
        try:
            existing_user = LineUser.query.filter_by(line_user_id=line_user_id).first()
            if not existing_user:
                # 創建新的 LineUser，is_subscribed 預設為 True
                new_user = LineUser(line_user_id=line_user_id, is_subscribed=True)
                db.session.add(new_user)
                db.session.commit()
                app.logger.info(f"新增 Line 用戶 (Follow Event): {line_user_id}")
                
                # 回覆歡迎訊息 (可選)
                line_bot_api.reply_message(
                    event.reply_token,
                    TextMessage(text='感謝您關注空氣品質監測機器人！您可以透過網頁設定警報，或者稍後我們將實現更多互動功能。')
                )
            else:
                # 如果用戶之前取消關注又重新關注，則將 is_subscribed 設為 True
                if not existing_user.is_subscribed:
                    existing_user.is_subscribed = True
                    db.session.commit()
                    app.logger.info(f"用戶 {line_user_id} 重新關注，is_subscribed 設為 True。")
                    line_bot_api.reply_message(
                        event.reply_token,
                        TextMessage(text='歡迎回來！很高興再次為您服務。')
                    )

        except Exception as e:
            db.session.rollback()
            app.logger.error(f"處理 Follow Event 時發生資料庫錯誤: {e}", exc_info=True)
            # 不向用戶回覆錯誤訊息，避免造成困擾

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event): # 更名為 handle_text_message 以避免與其他 MessageEvent 混淆
    line_user_id = event.source.user_id
    text = event.message.text
    app.logger.info(f"收到來自 {line_user_id} 的文字訊息: {text}")

    # 您可以在這裡加入更多文字指令處理，例如查詢特定測站等
    if "位置" in text or "地點" in text or "在哪" in text:
        reply_message = "請您點擊 LINE 聊天室左下角的「+」號，然後選擇「位置資訊」來分享您的當前位置，我將為您提供最即時的附近空氣品質資訊。"
        line_bot_api.reply_message(
            event.reply_token,
            TextMessage(text=reply_message)
        )
    else:
        # 預設回覆，可根據需要修改
        line_bot_api.reply_message(
            event.reply_token,
            TextMessage(text=f"您說了：『{text}』，歡迎使用空氣品質監測機器人！您可以點擊 LINE 聊天室左下角的「+」號，選擇「位置資訊」來分享您的當前位置，以便獲取附近的空氣品質資訊。")
        )

# 3. 新增處理 LocationMessage 的函式：
@handler.add(MessageEvent, message=LocationMessage)
def handle_location_message(event):
    line_user_id = event.source.user_id
    latitude = event.message.latitude
    longitude = event.message.longitude
    app.logger.info(f"收到來自 {line_user_id} 的位置訊息: 緯度 {latitude}, 經度 {longitude}")

    with app.app_context():
        try:
            line_user = LineUser.query.filter_by(line_user_id=line_user_id).first()
            if line_user:
                line_user.user_latitude = latitude
                line_user.user_longitude = longitude
                db.session.commit()
                app.logger.info(f"已更新用戶 {line_user_id} 的位置資訊。")
                
                # 找到最近的測站並立即回覆
                # 這裡調用您新的即時推送邏輯 (可以提取成一個 helper 函數)
                message = get_nearest_station_aqi_message(latitude, longitude)
                line_bot_api.reply_message(
                    event.reply_token,
                    TextMessage(text=message)
                )
            else:
                # 如果用戶還沒有在 LineUser 表中，可能是 FollowEvent 之前發送位置
                # 這情況應該很少見，但為了穩健性可以處理
                new_user = LineUser(line_user_id=line_user_id, user_latitude=latitude, user_longitude=longitude, is_subscribed=True)
                db.session.add(new_user)
                db.session.commit()
                app.logger.info(f"新增 Line 用戶並儲存位置資訊 (Location Event): {line_user_id}")
                message = get_nearest_station_aqi_message(latitude, longitude)
                line_bot_api.reply_message(
                    event.reply_token,
                    TextMessage(text=message)
                )

        except Exception as e:
            db.session.rollback()
            app.logger.error(f"處理 LocationMessage 或更新用戶位置時發生錯誤: {e}", exc_info=True)
            line_bot_api.reply_message(
                event.reply_token,
                TextMessage(text="抱歉，儲存您的位置時發生錯誤，請稍後再試。")
            )

# 4. 新增輔助函式：根據經緯度獲取最近的測站 AQI 訊息
def get_nearest_station_aqi_message(user_lat, user_lon):
    """
    根據用戶經緯度，找到最近的監測站，並生成其 AQI 訊息。
    """
    if user_lat is None or user_lon is None:
        return "未能取得您的位置資訊，請確認是否允許 LINE 應用程式取得位置權限。"

    with app.app_context():
        stations = Station.query.all()
        nearest_station = None
        min_distance = float('inf')

        for station in stations:
            if station.latitude is not None and station.longitude is not None:
                distance = calculate_distance(user_lat, user_lon, station.latitude, station.longitude)
                if distance < min_distance:
                    min_distance = distance
                    nearest_station = station
        
        if nearest_station:
            message = (
                f"您附近最近的測站是：{nearest_station.county} - {nearest_station.name}\n"
                f"距離：約 {min_distance:.2f} 公里\n"
                f"目前 AQI：{nearest_station.aqi if nearest_station.aqi is not None else 'N/A'} "
                f"({nearest_station.status if nearest_station.status else 'N/A'})\n"
                f"PM2.5：{nearest_station.pm25 if nearest_station.pm25 is not None else 'N/A'} µg/m³\n"
                f"PM10：{nearest_station.pm10 if nearest_station.pm10 is not None else 'N/A'} µg/m³\n"
                f"發布時間：{nearest_station.publish_time.strftime('%Y-%m-%d %H:%M') if nearest_station.publish_time else 'N/A'}\n"
                "\n保持健康，請注意空氣品質！"
            )
            return message
        else:
            return "抱歉，目前未能找到您附近的監測站數據。"

# 5. 新增排程任務：定期推送個人化 AQI 數據 (基於用戶位置)
@scheduler.task('interval', id='send_personalized_aqi_push_job', minutes=60, misfire_grace_time=600) # 每小時推送一次
def send_personalized_aqi_push_job():
    """
    排程任務：遍歷所有已提供位置資訊的用戶，推送其最近測站的最新空氣品質數據。
    為了避免過於頻繁的推送，可以設置一定的間隔或僅在數據有顯著變化時推送。
    """
    with app.app_context():
        app.logger.info(f"--- 排程任務: 正在發送個人化空氣品質推送 (基於用戶位置) ({datetime.now()}) ---")
        
        # 獲取所有已提供位置且仍然訂閱的用戶
        line_users_with_location = LineUser.query.filter(
            LineUser.is_subscribed == True,
            LineUser.user_latitude.isnot(None),
            LineUser.user_longitude.isnot(None)
        ).all()
        
        # 獲取所有監測站數據 (一次性取出，減少數據庫查詢)
        all_stations = Station.query.all()
        
        processed_count = 0
        for user in line_users_with_location:
            app.logger.info(f"為用戶 {user.line_user_id} 計算最近測站。")
            
            nearest_station = None
            min_distance = float('inf')

            for station in all_stations:
                if station.latitude is not None and station.longitude is not None:
                    distance = calculate_distance(user.user_latitude, user.user_longitude, station.latitude, station.longitude)
                    if distance < min_distance:
                        min_distance = distance
                        nearest_station = station
            
            if nearest_station:
                # 這裡可以加入一個簡單的機制，例如只在 AQI 狀態變化或達到某閾值時才推送
                # 目前先簡化為每小時推送一次最新的數據
                
                message = (
                    f"【您所在地區的最新空氣品質】\n"
                    f"測站：{nearest_station.county} - {nearest_station.name}\n"
                    f"距離您約 {min_distance:.2f} 公里\n"
                    f"目前 AQI：{nearest_station.aqi if nearest_station.aqi is not None else 'N/A'} "
                    f"({nearest_station.status if nearest_station.status else 'N/A'})\n"
                    f"PM2.5：{nearest_station.pm25 if nearest_station.pm25 is not None else 'N/A'} µg/m³\n"
                    f"PM10：{nearest_station.pm10 if nearest_station.pm10 is not None else 'N/A'} µg/m³\n"
                    f"發布時間：{nearest_station.publish_time.strftime('%Y-%m-%d %H:%M') if nearest_station.publish_time else 'N/A'}\n"
                    f"\n若要停止接收此通知，請封鎖本機器人。\n"
                    f"若要更新位置，請重新發送您的位置資訊。"
                )
                
                # 發送訊息並處理結果
                if send_line_message(user.line_user_id, message):
                    processed_count += 1
                # else: (send_line_message 內部已經會記錄錯誤)
            else:
                app.logger.warning(f"未能為用戶 {user.line_user_id} 找到最近的監測站。")
        
        app.logger.info(f"--- 排程任務: 已為 {processed_count} 個用戶發送個人化空氣品質推送 ---")


# 7. 修正 `handle_non_text_message` 避免重複處理 Unfollow 事件
# 原先的 handler.add(MessageEvent, message=None) 處理非文本訊息和 unfollow 事件
# 這會導致 unfollow 事件被處理兩次 (一次是作為 MessageEvent with None message，一次是作為 UnfollowEvent)
# 應該移除 MessageEvent, message=None 的 handler，讓 UnfollowEvent 專門處理
# 您的程式碼中的 `handle_unfollow` 已經被正確定義為處理 `event.type == 'unfollow'`
# 所以，您原始程式碼中的 `handle_non_text_message` 可以移除或修改為僅回覆非文字訊息。
# 我將其修改為只回覆非文字訊息，並將 Unfollow 事件的處理邏輯保留在 `handle_unfollow` 中。
@handler.add(MessageEvent, message=None) # 此處的 message=None 表示處理所有沒有特定類型匹配的 MessageEvent
def handle_other_message_types(event):
    line_user_id = event.source.user_id
    app.logger.info(f"收到來自 {line_user_id} 的非文本訊息或未知 MessageEvent 類型: {event.type}")
    
    # 這裡的邏輯是為了處理那些不是 TextMessage 和 LocationMessage 的 MessageEvent
    # 通常是貼圖、圖片、影片等。 Unfollow 事件會在 `handle_unfollow` 專門處理。
    line_bot_api.reply_message(
        event.reply_token,
        TextMessage(text="抱歉，我目前主要處理文字訊息和位置資訊。")
    )

@handler.add(MessageEvent) # 移除 message=None，這是一個通用處理 MessageEvent 的裝飾器
def handle_unfollow(event): # 此函數應更名為 handle_postback_and_other_events 或類似
    if event.type == 'unfollow':
        line_user_id = event.source.user_id
        app.logger.info(f"收到 Unfollow Event 來自用戶: {line_user_id}")
        with app.app_context():
            line_user = LineUser.query.filter_by(line_user_id=line_user_id).first()
            if line_user:
                line_user.is_subscribed = False # 將訂閱狀態設為 False
                db.session.commit()
                app.logger.info(f"用戶 {line_user_id} 取消關注，is_subscribed 設為 False。")
    # else:
    #     # 可以處理其他未明確定義的 MessageEvent 類型，例如 PostbackEvent
    #     app.logger.info(f"收到未處理的 MessageEvent 類型: {event.type}")

# 在應用程式上下文中執行資料庫初始化
with app.app_context():
    init_db()
    app.logger.info("資料庫初始化及表格創建已執行。")

# --- 運行應用程式 (此部分保持不變) ---
if __name__ == '__main__':
    # 在生產環境中，應使用 Gunicorn 或其他 WSGI 伺服器
    # 注意: 在本地調試時，如果您要測試 Webhook，需要使用 ngrok 等工具將本地服務暴露到公網
    app.run(host='0.0.0.0', port=5001, debug=True)