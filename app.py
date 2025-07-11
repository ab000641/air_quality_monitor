import os
import requests
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_apscheduler import APScheduler
from sqlalchemy.exc import IntegrityError
from datetime import datetime

from config import Config
from models import db, Station, LineNotifyBinding, BindingStation # 導入模型

# 創建 Flask 應用程式實例
app = Flask(__name__)
app.config.from_object(Config) # 載入配置

# 初始化資料庫
db.init_app(app)

# 初始化排程器
scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()

# --- 資料庫初始化函式 ---
def init_db():
    with app.app_context():
        # 檢查是否需要創建表格
        # 如果是空的資料庫，create_all 會創建所有定義的表格
        db.create_all()
        print("--- 資料庫表結構已完成 ---")
        # 首次啟動時，嘗試抓取並填充監測站數據
        if not Station.query.first(): # 如果 Stations 表是空的
            print("--- 正在首次抓取監測站資料並填充資料庫 ---")
            fetch_and_store_all_stations()
            print("--- 監測站資料填充完成 ---")
        else:
            print("--- 監測站資料已存在，跳過首次抓取 ---")

# --- 定義排程任務 ---
# 排程任務：每小時抓取一次即時空氣品質數據
@scheduler.task('interval', id='fetch_aqi_data_job', hours=1, misfire_grace_time=900)
def fetch_aqi_data_job():
    with app.app_context():
        print(f"--- 排程任務: 正在抓取即時空氣品質數據 ({datetime.now()}) ---")
        fetch_and_store_realtime_aqi()
        print("--- 排程任務: 即時空氣品質數據抓取完成 ---")

# 排程任務：每隔一段時間檢查警報並發送通知
@scheduler.task('interval', id='check_and_send_alerts_job', minutes=30, misfire_grace_time=600)
def check_and_send_alerts_job():
    with app.app_context():
        print(f"--- 排程任務: 正在檢查空氣品質警報並發送通知 ({datetime.now()}) ---")
        send_aqi_alerts()
        print("--- 排程任務: 空氣品質警報檢查完成 ---")

# --- API 互動函式 ---
def fetch_and_store_all_stations():
    """
    從環保署 API 抓取所有監測站列表並儲存到資料庫
    """
    api_url = Config.EPA_STATIONS_API_URL.format(api_key=Config.EPA_AQI_API_KEY)
    try:
        response = requests.get(api_url)
        response.raise_for_status() # 檢查 HTTP 錯誤
        data = response.json()
        
        if 'records' in data:
            for record in data['records']:
                # 根據實際 API 返回的 JSON 結構調整這裡的鍵名
                # 假設 API 返回的字段是 SiteName, SiteId, County, TWD97Lat, TWD97Lon
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
                        # 更新現有測站的資料，確保資料最新
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
        db.session.rollback() # 出錯時回滾事務
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
                # 根據實際 API 返回的 JSON 結構調整這裡的鍵名
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
                    # else:
                        # 如果有即時數據但找不到測站，可能是新測站，可以考慮新增或記錄日誌
                        # print(f"警告: 找不到測站 ID {site_id} 的紀錄，即時數據未更新。")
            db.session.commit()
            print(f"成功更新 {len(data['records'])} 個測站的即時 AQI 數據。")
        else:
            print("即時 AQI API 返回數據中未找到 'records' 鍵。")

    except requests.exceptions.RequestException as e:
        print(f"抓取即時 AQI 數據失敗: {e}")
    except Exception as e:
        db.session.rollback()
        print(f"處理即時 AQI 數據時發生錯誤: {e}")

# TODO: LINE Notify 相關函式 (稍後實作)
def send_line_notification(access_token, message):
    """
    發送 LINE Notify 通知
    """
    line_notify_api = 'https://notify-api.line.me/api/notify'
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    data = {
        'message': message
    }
    try:
        response = requests.post(line_notify_api, headers=headers, data=data)
        response.raise_for_status()
        print(f"LINE Notify 發送成功: {response.json()}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"LINE Notify 發送失敗: {e}")
        return False

def send_aqi_alerts():
    """
    檢查並發送 AQI 警報
    """
    # 獲取所有活躍的 LINE Notify 綁定
    active_bindings = LineNotifyBinding.query.filter_by(is_active=True).all()

    for binding in active_bindings:
        # 獲取此綁定關聯的所有測站
        for station in binding.stations:
            if station.aqi is not None and station.aqi >= binding.threshold_value:
                message = (
                    f"\n【空氣品質警報】\n"
                    f"測站：{station.county} - {station.name}\n"
                    f"目前 AQI：{station.aqi} ({station.status})\n"
                    f"發布時間：{station.publish_time.strftime('%Y-%m-%d %H:%M') if station.publish_time else 'N/A'}\n"
                    f"已超過您設定的閾值：{binding.threshold_value}！"
                )
                print(f"嘗試向 {binding.id} 發送警報: {station.name} AQI {station.aqi}")
                send_line_notification(binding.line_notify_token, message)
            # else:
            #     print(f"測站 {station.name} AQI {station.aqi} 未超過閾值 {binding.threshold_value}")


# --- 路由 (Routes) ---

@app.route('/')
def index():
    # 獲取所有監測站，用於前端下拉選單
    stations = Station.query.order_by(Station.county, Station.name).all()
    return render_template('index.html', stations=stations)

@app.route('/register_line_notify', methods=['GET'])
def register_line_notify():
    # 這個路由將引導用戶到 LINE Notify 授權頁面
    # LINE Notify 的 Client ID (您可以從 LINE Notify 服務中獲得)
    # 這裡可以放在 config.py 或 .env 中
    line_notify_client_id = os.getenv('LINE_NOTIFY_CLIENT_ID', 'YOUR_LINE_NOTIFY_CLIENT_ID') # <-- 替換成您的 Client ID

    # 回調 URL，LINE Notify 授權成功後會跳轉回這裡
    # 這裡的 URL 應該是您部署後的網域，例如 https://mynextproject.ab000641.site/line_notify_callback
    redirect_uri = url_for('line_notify_callback', _external=True)

    # 用戶選定的測站 ID 和閾值，透過 session 或其他方式傳遞
    # 由於是多對多，這裡需要處理多個站點 ID
    # 簡單起見，這裡先用查詢參數，但正式應用應更安全處理
    selected_station_ids = request.args.get('station_ids') # 假設逗號分隔的 ID
    threshold_value = request.args.get('threshold', type=int, default=100)

    # 將這些資訊存入 session 或暫存，以便回調時使用
    # session['selected_station_ids'] = selected_station_ids
    # session['threshold_value'] = threshold_value
    
    # 由於 LINE Notify 不支持 state 參數帶太多資料，這裡簡化處理
    # 正式應用可能需要一個臨時的數據庫表來儲存這些臨時資訊，然後在回調時用 state 參數去查詢
    # 或者讓用戶在回調後再選擇
    
    # LINE Notify 授權 URL
    line_auth_url = (
        f"https://notify-bot.line.me/oauth/authorize?"
        f"response_type=code&"
        f"client_id={line_notify_client_id}&"
        f"redirect_uri={redirect_uri}&"
        f"scope=notify&"
        f"state=YOUR_STATE_TOKEN_HERE" # 這裡可以放一個隨機字串用於安全性檢查，確保回調是合法的
    )
    # 如果要帶上選定的測站ID和閾值，需要將其編碼到state中或使用session，這裡先不處理複雜性
    
    return redirect(line_auth_url)


@app.route('/line_notify_callback', methods=['GET'])
def line_notify_callback():
    code = request.args.get('code')
    state = request.args.get('state') # 用於安全性檢查
    error = request.args.get('error')
    error_description = request.args.get('error_description')

    if error:
        flash(f"LINE Notify 授權失敗: {error_description}", "error")
        return redirect(url_for('index'))

    if not code:
        flash("LINE Notify 授權碼遺失。", "error")
        return redirect(url_for('index'))

    # 安全性檢查 (如果前面使用了 state)
    # if state != session.get('expected_state_token'):
    #    flash("安全性檢查失敗，請重試。", "error")
    #    return redirect(url_for('index'))

    # 從環境變數獲取 Client ID 和 Client Secret
    line_notify_client_id = os.getenv('LINE_NOTIFY_CLIENT_ID', 'YOUR_LINE_NOTIFY_CLIENT_ID')
    line_notify_client_secret = os.getenv('LINE_NOTIFY_CLIENT_SECRET', 'YOUR_LINE_NOTIFY_CLIENT_SECRET') # <-- 替換成您的 Client Secret

    # 回調 URL 必須與註冊 LINE Notify 服務時填寫的完全一致
    redirect_uri = url_for('line_notify_callback', _external=True)

    # 交換 access token
    token_url = "https://notify-bot.line.me/oauth/token"
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
        'client_id': line_notify_client_id,
        'client_secret': line_notify_client_secret
    }

    try:
        response = requests.post(token_url, headers=headers, data=data)
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get('access_token')

        if access_token:
            # TODO: 從 session 或其他方式獲取之前用戶選擇的 station_ids 和 threshold_value
            # 這裡簡化處理，假設用戶在回調後重新選擇或只有一個預設值
            
            # 假設這裡獲取到了用戶選擇的站點 ID 列表和閾值
            # 這是個臨時方案，因為 state 或 session 在 LINE 回調中可能不可靠
            # 正式做法：讓用戶在授權成功後，在另一個頁面再進行站點選擇和綁定
            # 或者在 register_line_notify 中用一個唯一的 ID 預儲存用戶選擇，然後在 state 中傳遞這個 ID
            temp_station_ids = [Station.query.first().id] if Station.query.first() else [] # 範例，取第一個站點 ID
            temp_threshold = 100 # 範例閾值

            with app.app_context():
                try:
                    # 檢查 token 是否已存在，避免重複綁定
                    existing_binding = LineNotifyBinding.query.filter_by(line_notify_token=access_token).first()
                    if not existing_binding:
                        new_binding = LineNotifyBinding(
                            line_notify_token=access_token,
                            threshold_value=temp_threshold # 這裡應該是用戶選擇的值
                        )
                        db.session.add(new_binding)
                        db.session.commit() # 先提交以獲取 new_binding.id

                        # 綁定測站 (多對多)
                        for station_id in temp_station_ids: # 這裡應該是從用戶選擇獲取
                            station = Station.query.get(station_id)
                            if station:
                                new_binding.stations.append(station)
                        db.session.commit()
                        flash("LINE Notify 綁定成功！", "success")
                    else:
                        flash("您已綁定 LINE Notify。", "info")
                except IntegrityError:
                    db.session.rollback()
                    flash("LINE Notify 綁定失敗：Token 已存在。", "error")
                except Exception as e:
                    db.session.rollback()
                    flash(f"綁定過程中發生錯誤: {e}", "error")
            return redirect(url_for('index'))
        else:
            flash("未能獲取 LINE Notify Access Token。", "error")
            return redirect(url_for('index'))

    except requests.exceptions.RequestException as e:
        flash(f"交換 LINE Notify Token 失敗: {e}", "error")
        return redirect(url_for('index'))

# --- 應用程式上下文 ---
@app.before_first_request
def create_tables():
    init_db()

# --- 運行應用程式 ---
if __name__ == '__main__':
    # 這裡的 run() 僅用於開發環境，生產環境應使用 Gunicorn
    app.run(host='0.0.0.0', port=5001, debug=True) # 確保監聽 5001 port