import os
import requests
import json
import hmac
import hashlib
import logging # 新增：導入 logging 模組

from flask import Flask, render_template, request, redirect, url_for, flash, session, abort
from flask_apscheduler import APScheduler
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, FollowEvent

from config import Config
from models import db, Station, LineUser, LineUserStationPreference

# 創建 Flask 應用程式實例
app = Flask(__name__)
app.config.from_object(Config)

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

# 初始化資料庫
db.init_app(app)

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

            # --- 新增：如果首次填充了測站資料，立即抓取並填充即時 AQI 數據 ---
            app.logger.info("--- 正在首次抓取即時空氣品質數據並填充資料庫 (即時) ---")
            fetch_and_store_realtime_aqi()
            app.logger.info("--- 即時空氣品質數據首次填充完成 ---")
            # -----------------------------------------------------------------
        else:
            app.logger.info("--- 監測站資料已存在，跳過首次抓取 ---")
            # 如果資料已存在，但即時數據可能過舊，可以選擇在這裡也觸發一次更新
            # 確保頁面載入時就有最新數據
            app.logger.info("--- 數據已存在，觸發即時數據更新 ---")
            fetch_and_store_realtime_aqi()
            app.logger.info("--- 即時數據更新完成 ---")


# --- 定義排程任務 ---
@scheduler.task('interval', id='fetch_aqi_data_job', hours=1, misfire_grace_time=900)
def fetch_aqi_data_job():
    with app.app_context():
        app.logger.info(f"--- 排程任務: 正在抓取即時空氣品質數據 ({datetime.now()}) ---")
        fetch_and_store_realtime_aqi()
        app.logger.info("--- 排程任務: 即時空氣品質數據抓取完成 ---")

@scheduler.task('interval', id='check_and_send_alerts_job', minutes=30, misfire_grace_time=600)
def check_and_send_alerts_job():
    with app.app_context():
        app.logger.info(f"--- 排程任務: 正在檢查空氣品質警報並發送通知 ({datetime.now()}) ---")
        send_aqi_alerts()
        app.logger.info("--- 排程任務: 空氣品質警報檢查完成 ---")

# --- API 互動函式 ---
def fetch_and_store_all_stations():
    """
    從環保署 API 抓取所有監測站列表並儲存到資料庫
    """
    api_url = Config.EPA_STATIONS_API_URL.format(api_key=Config.EPA_AQI_API_KEY)
    app.logger.info(f"嘗試從 API 獲取所有監測站資料。URL: {api_url}")
    try:
        response = requests.get(api_url)
        response.raise_for_status() # 對於 HTTP 錯誤狀態碼 (4xx 或 5xx) 拋出異常
        data = response.json()
        app.logger.info(f"API 返回數據 (部分): {json.dumps(data, indent=2)[:500]}...") # 打印部分返回數據

        if 'records' in data:
            new_stations_count = 0
            updated_stations_count = 0
            for record in data['records']:
                station_name = record.get('sitename')
                site_id = record.get('siteid')
                county = record.get('county')
                latitude = float(record.get('twd97lat')) if record.get('twd97lat') else None
                longitude = float(record.get('twd97lon')) if record.get('twd97lon') else None

                if site_id and station_name:
                    existing_station = Station.query.filter_by(site_id=site_id).first()
                    if not existing_station:
                        new_station = Station(
                            site_id=site_id,
                            name=station_name,
                            county=county,
                            latitude=latitude,
                            longitude=longitude
                        )
                        db.session.add(new_station)
                        new_stations_count += 1
                        app.logger.debug(f"新增測站: {station_name}") # 用 debug 級別避免刷屏
                    else:
                        existing_station.name = station_name
                        existing_station.county = county
                        existing_station.latitude = latitude
                        existing_station.longitude = longitude
                        updated_stations_count += 1
                        # app.logger.debug(f"更新測站: {station_name}") # 如果太多日誌，用 debug 級別
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


# --- LINE Messaging API 相關函式 ---

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
    # 獲取所有監測站，用於前端下拉選單
    stations = Station.query.order_by(Station.county, Station.name).all()
    app.logger.info(f"首頁請求：從資料庫獲取到 {len(stations)} 個測站資料。")
    return render_template('index.html', stations=stations)

# *** 手動綁定路由可以作為備用，但主要將透過 Webhook 獲取用戶 ID ***
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


# --- 新增 LINE Webhook 路由 ---
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

# --- 定義處理各種 LINE 事件的函式 ---
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
def handle_message(event):
    # 當用戶發送文本訊息時觸發
    # 這裡您可以根據用戶發送的內容實現更多互動功能 (例如查詢 AQI)
    line_user_id = event.source.user_id
    text = event.message.text
    app.logger.info(f"收到來自 {line_user_id} 的訊息: {text}")

    # 範例：簡單回覆用戶訊息 (可選)
    # line_bot_api.reply_message(
    #     event.reply_token,
    #     TextMessage(text=f"您說了：{text}")
    # )

@handler.add(MessageEvent, message=None) # 處理非文本訊息（例如貼圖、圖片、影片等）
def handle_non_text_message(event):
    line_user_id = event.source.user_id
    app.logger.info(f"收到來自 {line_user_id} 的非文本訊息。")
    # line_bot_api.reply_message(
    #     event.reply_token,
    #     TextMessage(text="抱歉，我目前只能處理文字訊息。")
    # )

# --- 處理 Unfollow 事件 (用戶封鎖 Bot) ---
@handler.add(MessageEvent, message=None) # 不指定 message 類型，以便處理所有事件
def handle_unfollow(event):
    if event.type == 'unfollow': # 判斷是否為 unfollow 事件
        line_user_id = event.source.user_id
        app.logger.info(f"收到 Unfollow Event 來自用戶: {line_user_id}")
        with app.app_context():
            line_user = LineUser.query.filter_by(line_user_id=line_user_id).first()
            if line_user:
                line_user.is_subscribed = False # 將訂閱狀態設為 False
                db.session.commit()
                app.logger.info(f"用戶 {line_user_id} 取消關注，is_subscribed 設為 False。")


# 在應用程式上下文中執行資料庫初始化
with app.app_context():
    init_db()
    app.logger.info("資料庫初始化及表格創建已執行。")

# --- 運行應用程式 ---
if __name__ == '__main__':
    # 在生產環境中，應使用 Gunicorn 或其他 WSGI 伺服器
    # 注意: 在本地調試時，如果您要測試 Webhook，需要使用 ngrok 等工具將本地服務暴露到公網
    app.run(host='0.0.0.0', port=5001, debug=True)