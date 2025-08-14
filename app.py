import os
import requests
import json
import hmac
import hashlib
import logging
from urllib.parse import urlencode

from flask import Flask, render_template, request, redirect, url_for, flash, session, abort, jsonify
from flask_apscheduler import APScheduler
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, FollowEvent, LocationMessage, UnfollowEvent

from config import Config
from models import db, Station, LineUser, LineUserStationPreference
from utils.distance import calculate_distance
from redis import Redis
from rq import Queue

redis_connection = Redis.from_url(os.getenv('REDIS_URL', 'redis://localhost:6379/0'))
aqi_queue = Queue('aqi_queue', connection=redis_connection)

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)

COUNTY_ORDER = [
    "基隆市", "臺北市", "新北市", "桃園市", "新竹市", "新竹縣", "苗栗縣", "臺中市", "彰化縣", "南投縣",
    "雲林縣", "嘉義市", "嘉義縣", "臺南市", "高雄市", "屏東縣", "宜蘭縣", "花蓮縣", "臺東縣", "澎湖縣",
    "金門縣", "連江縣"
]

COUNTY_TO_REGION = {
    "基隆市": "北", "臺北市": "北", "新北市": "北", "桃園市": "北", "新竹市": "北", "新竹縣": "北", "苗栗縣": "北",
    "臺中市": "中", "彰化縣": "中", "南投縣": "中", "雲林縣": "中",
    "嘉義市": "南", "嘉義縣": "南", "臺南市": "南", "高雄市": "南", "屏東縣": "南",
    "宜蘭縣": "東", "花蓮縣": "東", "臺東縣": "東",
    "澎湖縣": "離島", "金門縣": "離島", "連江縣": "離島"
}

REGION_ORDER = ["北", "中", "南", "東", "離島"]

AQI_STATUS_ORDER = [
    "良好",
    "普通",
    "對敏感族群不健康",
    "不健康",
    "非常不健康",
    "危害",
    "維護",
    "無效",
    "N/A",
    "未知"
]

STATUS_TO_CLASS_NAME = {
    "良好": "good",
    "普通": "moderate",
    "對敏感族群不健康": "unhealthy-for-sensitive",
    "不健康": "unhealthy",
    "非常不健康": "very-unhealthy",
    "危害": "hazardous",
    "維護": "maintenance",
    "無效": "invalid",
    "N/A": "na",
    "未知": "unknown"
}


app.secret_key = os.getenv('SECRET_KEY', 'a_very_secret_key_for_dev')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
app.logger.setLevel(logging.INFO)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
LINE_CHANNEL_ID = os.getenv('LINE_CHANNEL_ID') # --- 新增：LINE Channel ID ---

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ID:
    app.logger.warning("警告: LINE_CHANNEL_ACCESS_TOKEN、LINE_CHANNEL_SECRET 或 LINE_CHANNEL_ID 未設定，LINE 功能將受限。")

scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()

def init_db():
    with app.app_context():
        db.create_all()
        app.logger.info("--- 資料庫表結構已完成 ---")
        if not Station.query.first():
            app.logger.info("--- 正在首次抓取監測站資料並填充資料庫 ---")
            fetch_and_store_all_stations()
            app.logger.info("--- 監測站資料填充完成 ---")
            app.logger.info("--- 正在首次抓取即時空氣品質數據並填充資料庫 (即時) ---")
            fetch_and_store_realtime_aqi()
            app.logger.info("--- 即時空氣品質數據首次填充完成 ---")
        else:
            app.logger.info("--- 監測站資料已存在，跳過首次抓取與數據填充。排程器將處理後續更新。---")

@scheduler.task('cron', id='fetch_aqi_data_job', minute=5, misfire_grace_time=900)
def fetch_aqi_data_job():
    with app.app_context():
        app.logger.info(f"--- 排程任務: 正在抓取即時空氣品質數據 ({datetime.now()}) ---")
        fetch_and_store_realtime_aqi()
        app.logger.info("--- 排程任務: 即時空氣品質數據抓取完成 ---")
        app.logger.info("--- 排程任務: 即時空氣品質數據抓取完成。個人化通知將由獨立排程處理。---")

# 這裡我將您的 `fetch_and_store_all_stations` 和 `fetch_and_store_realtime_aqi` 函式保留不變
# 因為它們的邏輯是正確的。

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

                region = COUNTY_TO_REGION.get(county, "未知區域")

                if site_id and station_name:
                    existing_station = Station.query.filter_by(site_id=site_id).first()
                    if not existing_station:
                        new_station = Station(
                            site_id=site_id,
                            name=station_name,
                            county=county,
                            latitude=latitude,
                            longitude=longitude,
                            region=region
                        )
                        db.session.add(new_station)
                        new_stations_count += 1
                    else:
                        existing_station.name = station_name
                        existing_station.county = county
                        existing_station.latitude = latitude
                        existing_station.longitude = longitude
                        existing_station.region = region
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


def send_line_message(line_user_id, message_text):
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

@app.route('/')
def index():
    with app.app_context():
        all_stations = Station.query.order_by(Station.name).all()
        for station in all_stations:
            db.session.refresh(station)

        def get_county_order_key(station):
            try:
                return COUNTY_ORDER.index(station.county)
            except ValueError:
                return len(COUNTY_ORDER)

        stations = sorted(all_stations, key=get_county_order_key)

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
    with app.app_context():
        all_stations = Station.query.order_by(Station.name).all()
        def get_county_order_key(station):
            try:
                return COUNTY_ORDER.index(station.county)
            except ValueError:
                return len(COUNTY_ORDER)

        stations = sorted(all_stations, key=get_county_order_key)

        data = []
        for station in stations:
            db.session.refresh(station)
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
                'status_class_name': status_class_name
            })
        
        return jsonify(data)

# --- 修改：將 manual_line_binding 路由改名為 preferences，並進行修改 ---
@app.route('/preferences', methods=['GET', 'POST'])
def preferences():
    # 檢查 session 中是否有 line_user_id，如果沒有則重導向回首頁
    line_user_id = session.get('line_user_id')
    if not line_user_id:
        flash("請先使用 LINE 登入。", "warning")
        return redirect(url_for('index'))

    # 獲取已選擇的偏好設定
    with app.app_context():
        user_preferences = LineUserStationPreference.query.filter_by(line_user_id=line_user_id).all()
        user_station_ids = {pref.station_id for pref in user_preferences}
        default_threshold = 100
        if user_preferences:
            # 獲取任一已設定的閾值作為預設值
            default_threshold = user_preferences[0].threshold_value

    if request.method == 'POST':
        # 在 POST 請求中處理表單提交
        selected_station_ids = request.form.getlist('station_ids')
        threshold_value = request.form.get('threshold', type=int, default=100)
        
        # 處理取消訂閱的情況
        if not selected_station_ids:
            with app.app_context():
                LineUserStationPreference.query.filter_by(line_user_id=line_user_id).delete()
                db.session.commit()
                flash("已取消所有測站的訂閱。", "success")
                app.logger.info(f"用戶 {line_user_id} 已取消所有測站訂閱。")
            return redirect(url_for('preferences'))

        station_ids = [int(sid) for sid in selected_station_ids if sid.isdigit()]
        if not station_ids:
            flash("無效的監測站選擇。", "error")
            return redirect(url_for('preferences'))

        with app.app_context():
            try:
                # 獲取或創建 LineUser
                line_user = LineUser.query.filter_by(line_user_id=line_user_id).first()
                if not line_user:
                    line_user = LineUser(line_user_id=line_user_id, default_threshold=threshold_value, is_subscribed=True)
                    db.session.add(line_user)
                    db.session.commit()
                else:
                    line_user.default_threshold = threshold_value
                    line_user.is_subscribed = True
                    db.session.commit()

                # 處理新增和移除的偏好設定
                existing_preferences = LineUserStationPreference.query.filter_by(line_user_id=line_user_id).all()
                existing_station_ids = {pref.station_id for pref in existing_preferences}
                
                # 移除不再需要的偏好
                for pref in existing_preferences:
                    if pref.station_id not in station_ids:
                        db.session.delete(pref)
                        app.logger.info(f"用戶 {line_user_id} 移除測站 {pref.station.name} 的訂閱。")

                # 新增或更新需要的偏好
                for station_id in station_ids:
                    if station_id not in existing_station_ids:
                        new_preference = LineUserStationPreference(
                            line_user_id=line_user_id,
                            station_id=station_id,
                            threshold_value=threshold_value
                        )
                        db.session.add(new_preference)
                        app.logger.info(f"用戶 {line_user_id} 新增測站 {station_id} 的訂閱。")
                    else:
                        # 只需要更新閾值，因為關係已經存在
                        existing_pref = next(p for p in existing_preferences if p.station_id == station_id)
                        existing_pref.threshold_value = threshold_value
                        app.logger.info(f"用戶 {line_user_id} 更新測站 {station_id} 的閾值。")
                
                db.session.commit()
                flash("LINE 訂閱和測站設定已成功更新！", "success")

            except Exception as e:
                db.session.rollback()
                flash(f"更新設定過程中發生錯誤: {e}", "error")
                app.logger.error(f"更新設定過程中發生錯誤: {e}", exc_info=True)
        
        return redirect(url_for('preferences'))
    
    # 渲染頁面時，顯示所有測站和用戶當前的選擇
    stations = Station.query.order_by(Station.county, Station.name).all()
    return render_template('preferences.html', stations=stations, user_station_ids=user_station_ids, default_threshold=default_threshold)

# --- 新增：處理 LINE 登出 (清除 Session) ---
@app.route('/logout')
def logout():
    session.pop('line_user_id', None)
    flash('您已成功登出。', 'success')
    return redirect(url_for('index'))

# --- 新增：LINE 登入路由 ---
@app.route('/line_login')
def line_login():
    """
    導向 LINE Login 授權頁面
    """
    # 確保回呼網址 (redirect_uri) 與您在 LINE Developers Console 中設定的一致
    # 例如：http://localhost:5001/line_callback
    redirect_uri = url_for('line_callback', _external=True)
    state = 'a_secure_state_string_for_csrf_protection' # 建議使用更複雜的隨機字串

    session['line_state'] = state # 將 state 儲存到 session 中以備後續驗證

    auth_url = 'https://access.line.me/oauth2/v2.1/authorize?' + urlencode({
        'response_type': 'code',
        'client_id': LINE_CHANNEL_ID,
        'redirect_uri': redirect_uri,
        'state': state,
        'scope': 'profile openid',
    })
    
    return redirect(auth_url)

# --- 新增：LINE 登入回呼路由 ---
@app.route('/line_callback')
def line_callback():
    """
    接收來自 LINE Login 的回呼
    """
    code = request.args.get('code')
    state = request.args.get('state')

    # 1. 驗證 state 以防 CSRF 攻擊
    if 'line_state' not in session or state != session['line_state']:
        app.logger.error("LINE 登入失敗：State 驗證失敗。")
        flash("登入失敗，請稍後再試。", "error")
        return redirect(url_for('index'))
    
    session.pop('line_state', None) # 驗證後移除 session 中的 state
    
    # 2. 用 code 換取 access token
    try:
        token_url = 'https://api.line.me/oauth2/v2.1/token'
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        data = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': url_for('line_callback', _external=True),
            'client_id': LINE_CHANNEL_ID,
            'client_secret': LINE_CHANNEL_SECRET,
        }
        
        response = requests.post(token_url, headers=headers, data=data)
        response.raise_for_status() # 如果響應不是 200 OK，則引發 HTTPError
        token_data = response.json()
        access_token = token_data.get('access_token')
        
        # 3. 用 access token 換取用戶資料 (profile)
        profile_url = 'https://api.line.me/v2/profile'
        profile_headers = {'Authorization': f'Bearer {access_token}'}
        profile_response = requests.get(profile_url, headers=profile_headers)
        profile_response.raise_for_status()
        profile_data = profile_response.json()
        
        line_user_id = profile_data.get('userId')
        
        if line_user_id:
            # 將 LINE 用戶 ID 儲存到 session
            session['line_user_id'] = line_user_id
            
            # 在資料庫中創建或更新 LineUser
            with app.app_context():
                existing_user = LineUser.query.filter_by(line_user_id=line_user_id).first()
                if not existing_user:
                    # 如果用戶不存在，創建一個新的 LineUser
                    new_user = LineUser(line_user_id=line_user_id)
                    db.session.add(new_user)
                    db.session.commit()
                    app.logger.info(f"新用戶透過 LINE 登入: {line_user_id}")
                    flash("您已成功使用 LINE 登入！請選擇您想追蹤的測站。", "success")
                    # 新用戶導向設定偏好頁面
                    return redirect(url_for('preferences'))
                else:
                    app.logger.info(f"用戶 {line_user_id} 重新登入。")
                    flash("您已成功使用 LINE 登入！", "success")
                    # 舊用戶導向設定偏好頁面
                    return redirect(url_for('preferences'))
        else:
            app.logger.error("LINE 登入失敗：未能從用戶資料中取得用戶 ID。")
            flash("登入失敗，請稍後再試。", "error")
            return redirect(url_for('index'))
            
    except requests.exceptions.RequestException as e:
        app.logger.error(f"LINE 登入流程中發生網路錯誤: {e}", exc_info=True)
        flash("登入失敗，網路錯誤。請稍後再試。", "error")
        return redirect(url_for('index'))
    except Exception as e:
        app.logger.error(f"LINE 登入流程中發生未知錯誤: {e}", exc_info=True)
        flash("登入失敗，發生未知錯誤。請稍後再試。", "error")
        return redirect(url_for('index'))

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info(f"收到 LINE Webhook 請求。請求體: {body[:500]}...")

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.error("簽名驗證失敗。請檢查您的 Channel Secret。")
        abort(400)
    except Exception as e:
        app.logger.error(f"處理 Webhook 事件時發生錯誤: {e}", exc_info=True)
        abort(500)

    return 'OK'

@handler.add(FollowEvent)
def handle_follow(event):
    line_user_id = event.source.user_id
    app.logger.info(f"收到 Follow Event 來自用戶: {line_user_id}")
    
    with app.app_context():
        try:
            existing_user = LineUser.query.filter_by(line_user_id=line_user_id).first()
            if not existing_user:
                new_user = LineUser(line_user_id=line_user_id, is_subscribed=True)
                db.session.add(new_user)
                db.session.commit()
                app.logger.info(f"新增 Line 用戶 (Follow Event): {line_user_id}")
                
                line_bot_api.reply_message(
                    event.reply_token,
                    TextMessage(text='感謝您關注空氣品質監測機器人！請使用網頁登入並設定您想追蹤的測站。')
                )
            else:
                if not existing_user.is_subscribed:
                    existing_user.is_subscribed = True
                    db.session.commit()
                    app.logger.info(f"用戶 {line_user_id} 重新關注，is_subscribed 設為 True。")
                    line_bot_api.reply_message(
                        event.reply_token,
                        TextMessage(text='歡迎回來！很高興再次為您服務。請前往網站設定您的偏好。')
                    )

        except Exception as e:
            db.session.rollback()
            app.logger.error(f"處理 Follow Event 時發生資料庫錯誤: {e}", exc_info=True)

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    line_user_id = event.source.user_id
    text = event.message.text.strip()
    app.logger.info(f"收到來自 {line_user_id} 的文字訊息: {text}")

    # 這段文字處理邏輯保持不變
    # ... (您的原始 handle_text_message 內容)
    # 這裡我將您的原始 handle_text_message 函式保留不變，因為它已經處理了文字指令和測站訂閱
    with app.app_context():
        station_to_subscribe = Station.query.filter_by(name=text).first()
        if station_to_subscribe:
            try:
                line_user = LineUser.query.filter_by(line_user_id=line_user_id).first()
                if not line_user:
                    line_user = LineUser(line_user_id=line_user_id)
                    db.session.add(line_user)
                    db.session.commit()

                existing_preference = LineUserStationPreference.query.filter_by(
                    line_user_id=line_user_id,
                    station_id=station_to_subscribe.id
                ).first()

                if not existing_preference:
                    new_preference = LineUserStationPreference(
                        line_user_id=line_user_id,
                        station_id=station_to_subscribe.id,
                        threshold_value=line_user.default_threshold
                    )
                    db.session.add(new_preference)
                    db.session.commit()
                    reply_message = f"您已成功訂閱『{station_to_subscribe.name}』站點的空氣品質警報！"
                    app.logger.info(f"成功為用戶 {line_user_id} 新增 {station_to_subscribe.name} 的訂閱。")
                else:
                    reply_message = f"您已經訂閱過『{station_to_subscribe.name}』站點的警報了喔！"
                    app.logger.info(f"用戶 {line_user_id} 已經訂閱過 {station_to_subscribe.name}。")

                line_bot_api.reply_message(
                    event.reply_token,
                    TextMessage(text=reply_message)
                )

            except Exception as e:
                db.session.rollback()
                app.logger.error(f"儲存用戶訂閱時發生錯誤: {e}", exc_info=True)
                line_bot_api.reply_message(
                    event.reply_token,
                    TextMessage(text="抱歉，儲存您的訂閱時發生錯誤，請稍後再試。")
                )

        elif "位置" in text or "地點" in text or "在哪" in text:
            reply_message = "請您點擊 LINE 聊天室左下角的「+」號，然後選擇「位置資訊」來分享您的當前位置，我將為您提供最即時的附近空氣品質資訊。"
            line_bot_api.reply_message(
                event.reply_token,
                TextMessage(text=reply_message)
            )
        else:
            reply_message = (
                f"您說了：『{text}』，歡迎使用空氣品質監測機器人！\n"
                f"您可以發送『台北』、『台中』等監測站名稱來訂閱警報。\n"
                f"或者點擊 LINE 聊天室左下角的「+」號，選擇「位置資訊」來獲取附近的空氣品質資訊。"
            )
            line_bot_api.reply_message(
                event.reply_token,
                TextMessage(text=reply_message)
            )

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
                message = get_nearest_station_aqi_message(latitude, longitude)
                line_bot_api.reply_message(
                    event.reply_token,
                    TextMessage(text=message)
                )
            else:
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

def get_nearest_station_aqi_message(user_lat, user_lon):
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

@scheduler.task('cron', id='send_personalized_aqi_push_job', minute=10, misfire_grace_time=600)
def send_personalized_aqi_push_job():
    with app.app_context():
        app.logger.info(f"--- 排程任務: 正在發送個人化空氣品質推送 (基於用戶位置) ({datetime.now()}) ---")
        
        line_users_with_location = LineUser.query.filter(
            LineUser.is_subscribed == True,
            LineUser.user_latitude.isnot(None),
            LineUser.user_longitude.isnot(None)
        ).all()
        
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
                
                if send_line_message(user.line_user_id, message):
                    processed_count += 1
            else:
                app.logger.warning(f"未能為用戶 {user.line_user_id} 找到最近的監測站。")
        
        app.logger.info(f"--- 排程任務: 已為 {processed_count} 個用戶發送個人化空氣品質推送 ---")


# --- 新增：新的排程任務：發送基於偏好設定的警報 ---
@scheduler.task('cron', id='send_personalized_aqi_alerts', minute=10, misfire_grace_time=600)
def send_personalized_aqi_alerts_job():
    """
    排程任務：檢查所有設定了偏好監測站的用戶，並在 AQI 超過閾值時發送警報。
    """
    with app.app_context():
        app.logger.info(f"--- 排程任務: 正在檢查用戶偏好並發送警報 ({datetime.now()}) ---")

        # 獲取所有設定了偏好且仍然訂閱的用戶
        preferences = LineUserStationPreference.query.filter(
            LineUserStationPreference.line_user.has(LineUser.is_subscribed == True)
        ).all()
        
        processed_count = 0
        for pref in preferences:
            user = pref.line_user
            station = pref.station
            
            # 確保獲取最新數據
            db.session.refresh(station)
            
            # 如果 AQI 超過閾值，且距離上次發送警報的時間超過設定間隔（例如 1 小時）
            # 這一段邏輯可以根據您的需求調整
            threshold = pref.threshold_value
            aqi = station.aqi

            # 確保 AQI 有效，且大於閾值
            if aqi is not None and aqi >= threshold:
                # 檢查上次發送時間，避免重複發送
                time_since_last_alert = datetime.now() - pref.last_alert_sent_at if pref.last_alert_sent_at else None
                
                # 如果是第一次發送，或者距離上次發送已超過 1 小時 (3600 秒)
                if time_since_last_alert is None or time_since_last_alert > timedelta(hours=1):
                    message = (
                        f"【空氣品質警報】\n"
                        f"您追蹤的測站『{station.name}』的空氣品質已超過您設定的警報閾值！\n"
                        f"目前 AQI：{aqi} ({station.status})\n"
                        f"發布時間：{station.publish_time.strftime('%Y-%m-%d %H:%M') if station.publish_time else 'N/A'}\n"
                    )
                    
                    if send_line_message(user.line_user_id, message):
                        pref.last_alert_sent_at = datetime.now() # 更新上次發送時間
                        db.session.commit()
                        processed_count += 1
                        app.logger.info(f"成功為用戶 {user.line_user_id} 發送『{station.name}』的警報。")

        app.logger.info(f"--- 排程任務: 已為 {processed_count} 個用戶發送基於偏好的警報 ---")


@handler.add(MessageEvent, message=None)
def handle_other_message_types(event):
    line_user_id = event.source.user_id
    app.logger.info(f"收到來自 {line_user_id} 的非文本訊息或未知 MessageEvent 類型: {event.message.type}")
    
    line_bot_api.reply_message(
        event.reply_token,
        TextMessage(text="抱歉，我目前主要處理文字訊息和位置資訊。")
    )

@handler.add(UnfollowEvent) # --- 修改：使用 UnfollowEvent 專門處理取消關注事件 ---
def handle_unfollow_event(event):
    line_user_id = event.source.user_id
    app.logger.info(f"收到 UnfollowEvent 來自用戶: {line_user_id}")
    with app.app_context():
        line_user = LineUser.query.filter_by(line_user_id=line_user_id).first()
        if line_user:
            line_user.is_subscribed = False
            db.session.commit()
            app.logger.info(f"用戶 {line_user_id} 取消關注，is_subscribed 設為 False。")

with app.app_context():
    init_db()
    app.logger.info("資料庫初始化及表格創建已執行。")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)