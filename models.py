from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

# 監測站模型
class Station(db.Model):
    __tablename__ = 'stations' # 表格名稱

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.String(50), unique=True, nullable=False) # 環保署測站代碼，保持唯一
    name = db.Column(db.String(100), nullable=False) # 測站名稱 (例如 "萬華")
    county = db.Column(db.String(50), nullable=False) # 縣市
    latitude = db.Column(db.Float) # 緯度
    longitude = db.Column(db.Float) # 經度
    # 可以添加最新 AQI 數據的欄位，方便查詢和警報判斷
    region = db.Column(db.String(50)) # 測站所屬的地理區域 (例如 "北", "中", "南", "東", "離島")
    aqi = db.Column(db.Integer)
    status = db.Column(db.String(50))
    pm25 = db.Column(db.Integer)
    pm10 = db.Column(db.Integer)
    publish_time = db.Column(db.DateTime) # AQI 發布時間

    # 與 LineUser 的多對多關係，透過 LineUserStationPreference 中間表
    # back_populates 將在 LineUser 模型中定義對應的關係
    user_preferences = db.relationship(
        'LineUserStationPreference',
        back_populates='station',
        cascade="all, delete-orphan" # 當測站被刪除時，刪除相關的偏好設定
    )

    def __repr__(self):
        return f"<Station {self.name} ({self.county})>"

# Line 用戶模型 (取代 LineNotifyBinding)
class LineUser(db.Model):
    __tablename__ = 'line_users' # 表格名稱

    id = db.Column(db.Integer, primary_key=True)
    line_user_id = db.Column(db.String(50), unique=True, nullable=False) # Line 用戶的唯一 ID
    is_subscribed = db.Column(db.Boolean, default=True) # 用戶是否仍然訂閱 (例如，是否封鎖了 Bot)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # 這裡可以儲存一些用戶的預設偏好，例如預設警報閾值
    default_threshold = db.Column(db.Integer, default=100) # 用戶預設的警報閾值

    # 與 Station 的多對多關係，透過 LineUserStationPreference 中間表
    stations_preferences = db.relationship(
        'LineUserStationPreference',
        back_populates='line_user',
        cascade="all, delete-orphan" # 當用戶被刪除時，刪除相關的偏好設定
    )

    def __repr__(self):
        return f"<LineUser {self.line_user_id} Subscribed: {self.is_subscribed}>"

# 用戶測站偏好設定模型 (多對多中間表，取代 BindingStation)
class LineUserStationPreference(db.Model):
    __tablename__ = 'line_user_station_preferences' # 表格名稱

    line_user_id = db.Column(db.String(50), db.ForeignKey('line_users.line_user_id'), primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey('stations.id'), primary_key=True)
    
    # 用戶為這個特定測站設定的閾值 (如果沒有設定，可以繼承 LineUser 的 default_threshold)
    threshold_value = db.Column(db.Integer, default=100) 
    
    # 可以添加一個上次發送警報的時間戳，避免重複發送
    last_alert_sent_at = db.Column(db.DateTime)

    # 建立關係
    line_user = db.relationship('LineUser', back_populates='stations_preferences')
    station = db.relationship('Station', back_populates='user_preferences')

    def __repr__(self):
        return (f"<LineUserStationPreference UserID: {self.line_user_id}, "
                f"StationID: {self.station_id}, Threshold: {self.threshold_value}>")