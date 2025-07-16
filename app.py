import os
import requests
from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_apscheduler import APScheduler
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta

# 從 models.py 導入模型
from config import Config
from models import db, Station, LineUser, LineUserStationPreference

# 創建 Flask 應用程式實例
app = Flask(__name__)
app.config.from_object(Config) # 載入配置

# 設置 Flask session 的密鑰，在生產環境中必須設置並使用複雜的隨機字串
app.secret_key = os.getenv('SECRET_KEY', 'a_very_secret_key_for_dev')

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
        print("--- 資料庫表結構已完成 ---")
        if not Station.query.first():
            print("--- 正在首次抓取監測站資料並填充資料庫 ---")
            fetch_and_store_all_stations()
            print("--- 監測站資料填充完成 ---")
        else:
            print("--- 監測站資料已存在，跳過首次抓取 ---")

# --- 定義排程任務 ---
@scheduler.task('interval', id='fetch_aqi_data_job', hours=1, misfire_grace_time=900)
def fetch_aqi_data_job():
    with app.app_context():
        print(f"--- 排程任務: 正在抓取即時空氣品質數據 ({datetime.now()}) ---")
        fetch_and_store_realtime_aqi()
        print("--- 排程任務: 即時空氣品質數據抓取完成 ---")

@scheduler.task('interval', id='check_and_send_alerts_job', minutes=30, misfire_grace_time=600)
def check_and_send_alerts_job():
    with app.app_context():
        print(f"--- 排程任務: 正在檢查空氣品質警報並發送通知 ({datetime.now()}) ---")
        send_aqi_alerts()
        print("--- 排程任務: 空氣品質警報檢查完成 ---")

# --- API 互動函式 (與 LINE API 無關的部分保持不變) ---
def fetch_and_store_all_stations():
    """
    從環保署 API 抓取所有監測站列表並儲存到資料庫
    """
    api_url = Config.EPA_STATIONS_API_URL.format(api_key=Config.EPA_AQI_API_KEY)
    try:
        response = requests.get(api_url)
        response.raise_for_status()
        data = response.json()
        
        if 'records' in data:
            for record in data['records']:
                station_name = record.get('SiteName')
                site_id = record.get('SiteId')
                county = record.get('County')
                latitude = float(record.get('TWD97Lat')) if record.get('TWD97Lat') else None
                longitude = float(record.get('TWD97Lon')) if record.get('TWD97Lon') else None

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
                        print(f"新增測站: {station_name}")
                    else:
                        existing_station.name = station_name
                        existing_station.county = county
                        existing_station.latitude = latitude
                        existing_station.longitude = longitude
                        print(f"更新測站: {station_name}")
            db.session.commit()
            print(f"成功抓取並儲存 {len(data['records'])} 個監測站資料。")
        else:
            print("API 返回數據中未找到 'records' 鍵。")

    except requests.exceptions.RequestException as e:
        print(f"抓取監測站資料失敗: {e}")
    except Exception as e:
        db.session.rollback()
        print(f"處理監測站資料時發生錯誤: {e}")


def fetch_and_store_realtime_aqi():
    """
    從環保署 API 抓取即時空氣品質數據並更新到資料庫的 Station 表中
    """
    api_url = Config.EPA_AQI_REALTIME_API_URL.format(api_key=Config.EPA_AQI_API_KEY)
    try:
        response = requests.get(api_url)
        response.raise_for_status()
        data = response.json()

        if 'records' in data:
            for record in data['records']:
                site_id = record.get('SiteId')
                aqi = int(record.get('AQI')) if record.get('AQI') and record.get('AQI').isdigit() else None
                status = record.get('Status')
                pm25 = int(record.get('PM2.5')) if record.get('PM2.5') and record.get('PM2.5').isdigit() else None
                pm10 = int(record.get('PM10')) if record.get('PM10') and record.get('PM10').isdigit() else None
                publish_time_str = record.get('PublishTime')
                publish_time = datetime.strptime(publish_time_str, '%Y-%m-%d %H:%M') if publish_time_str else None

                if site_id:
                    station = Station.query.filter_by(site_id=site_id).first()
                    if station:
                        station.aqi = aqi
                        station.status = status
                        station.pm25 = pm25
                        station.pm10 = pm10
                        station.publish_time = publish_time
            db.session.commit()
            print(f"成功更新 {len(data['records'])} 個測站的即時 AQI 數據。")
        else:
            print("即時 AQI API 返回數據中未找到 'records' 鍵。")

    except requests.exceptions.RequestException as e:
        print(f"抓取即時 AQI 數據失敗: {e}")
    except Exception as e:
        db.session.rollback()
        print(f"處理即時 AQI 數據時發生錯誤: {e}")


# --- 新增 LINE Messaging API 相關函式 ---

def send_line_message(line_user_id, message_text):
    """
    使用 LINE Messaging API 發送推播訊息給指定用戶
    """
    line_api_url = "https://api.line.me/v2/bot/message/push"
    channel_access_token = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')

    if not channel_access_token:
        print("錯誤: LINE_CHANNEL_ACCESS_TOKEN 未設定，無法發送 LINE 訊息。")
        return False

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {channel_access_token}'
    }
    
    # LINE Messaging API 的訊息格式
    payload = {
        'to': line_user_id,
        'messages': [
            {
                'type': 'text',
                'text': message_text
            }
        ]
    }

    try:
        response = requests.post(line_api_url, headers=headers, json=payload)
        response.raise_for_status() # 檢查 HTTP 錯誤
        print(f"LINE 訊息發送成功給 {line_user_id}: {response.json()}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"LINE 訊息發送失敗給 {line_user_id}: {e}")
        # 詳細錯誤訊息通常在 response.text
        if response.status_code == 400: # Bad Request
            print(f"LINE API 錯誤響應: {response.text}")
        return False

def send_aqi_alerts():
    """
    檢查並發送 AQI 警報 (現在使用 LINE Messaging API)
    """
    user_station_preferences = LineUserStationPreference.query.all()

    for preference in user_station_preferences:
        line_user = preference.line_user
        station = preference.station
        threshold_value = preference.threshold_value

        # 確保 LineUser 活躍且測站存在
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
                print(f"嘗試向 Line 用戶 {line_user.line_user_id} (Station: {station.name}) 發送警報: AQI {station.aqi}")
                
                # 使用新的 send_line_message 函式
                if send_line_message(line_user.line_user_id, message):
                    preference.last_alert_sent_at = datetime.utcnow()
                    db.session.commit()
                else:
                    print(f"警告: 無法發送 LINE 訊息給用戶 {line_user.line_user_id}。")
            # else:
            #     print(f"測站 {station.name} AQI {station.aqi} 未超過閾值 {threshold_value} 或未到發送間隔。")


# --- 路由 (Routes) ---

@app.route('/')
def index():
    # 獲取所有監測站，用於前端下拉選單
    stations = Station.query.order_by(Station.county, Station.name).all()
    # 這裡的表單應該允許用戶輸入 LINE User ID 和選擇測站
    # 或者，您會引導用戶到 LINE 官方帳號添加 Bot 好友，然後由 Bot 收集 ID
    return render_template('index.html', stations=stations)

# *** 由於轉向 LINE Messaging API，此路由將被移除或大幅簡化 ***
# LINE Messaging API 的用戶綁定通常不是透過這種 OAuth 回調機制
# 而是透過 Webhook 接收用戶的 "follow" 事件來獲取 line_user_id
# 這裡將提供一個簡化的頁面，用於手動綁定 (開發測試用)
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
                # 查找或創建 LineUser
                line_user = LineUser.query.filter_by(line_user_id=line_user_id).first()
                if not line_user:
                    line_user = LineUser(line_user_id=line_user_id, default_threshold=threshold_value, is_subscribed=True)
                    db.session.add(line_user)
                    db.session.commit() # 提交以獲取 line_user.id
                    flash(f"已新增 LINE 用戶: {line_user_id}", "info")
                else:
                    line_user.default_threshold = threshold_value # 更新預設閾值
                    line_user.is_subscribed = True # 確保標記為訂閱狀態
                    db.session.commit()
                    flash(f"已更新 LINE 用戶: {line_user_id}", "info")

                # 處理測站綁定
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
                            print(f"綁定測站 {station.name} 到用戶 {line_user.line_user_id}")
                        else:
                            existing_preference.threshold_value = threshold_value
                            print(f"更新用戶 {line_user.line_user_id} 在測站 {station.name} 的閾值")
                db.session.commit()
                flash("LINE 訂閱和測站綁定成功！", "success")

            except IntegrityError:
                db.session.rollback()
                flash("綁定失敗：資料庫重複或錯誤。", "error")
            except Exception as e:
                db.session.rollback()
                flash(f"綁定過程中發生錯誤: {e}", "error")
        
        return redirect(url_for('index'))
    
    # GET 請求顯示表單
    stations = Station.query.order_by(Station.county, Station.name).all()
    return render_template('manual_line_binding.html', stations=stations)


# --- 刪除 LINE Notify OAuth 回調路由 ---
# @app.route('/register_line_notify', methods=['GET'])
# @app.route('/line_notify_callback', methods=['GET'])
# 這兩個路由不再適用於 LINE Messaging API，因為用戶加入 Bot 的方式不同


# 在應用程式上下文中執行資料庫初始化
with app.app_context():
    init_db()
    print("資料庫初始化及表格創建已執行。")

# --- 運行應用程式 ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)