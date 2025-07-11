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
    aqi = db.Column(db.Integer)
    status = db.Column(db.String(50))
    pm25 = db.Column(db.Integer)
    pm10 = db.Column(db.Integer)
    publish_time = db.Column(db.DateTime) # AQI 發布時間

    # 與 LineNotifyBinding 的多對多關係
    bindings = db.relationship(
        'LineNotifyBinding',
        secondary='binding_stations',
        back_populates='stations'
    )

    def __repr__(self):
        return f"<Station {self.name} ({self.county})>"

# LINE 通知綁定模型
class LineNotifyBinding(db.Model):
    __tablename__ = 'line_notify_bindings' # 表格名稱

    id = db.Column(db.Integer, primary_key=True)
    # LINE Notify Token 必須加密儲存，這裡先用 VARCHAR 作為範例
    # 在實際生產環境，應使用加密方法，例如 Fernet
    line_notify_token = db.Column(db.String(255), unique=True, nullable=False)
    threshold_value = db.Column(db.Integer, default=100) # 用戶設定的警報閾值，預設 100 (黃色警戒)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True) # 是否啟用此通知

    # 與 Station 的多對多關係
    stations = db.relationship(
        'Station',
        secondary='binding_stations',
        back_populates='bindings'
    )

    def __repr__(self):
        return f"<LineNotifyBinding {self.id} Active: {self.is_active}>"

# 綁定-測站關聯模型 (多對多中間表)
class BindingStation(db.Model):
    __tablename__ = 'binding_stations' # 表格名稱

    binding_id = db.Column(db.Integer, db.ForeignKey('line_notify_bindings.id'), primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey('stations.id'), primary_key=True)

    # 建立關係，可以直接透過 binding_station 存取相關物件
    binding = db.relationship('LineNotifyBinding', backref=db.backref('station_associations', cascade="all, delete-orphan"))
    station = db.relationship('Station', backref=db.backref('binding_associations', cascade="all, delete-orphan"))

    def __repr__(self):
        return f"<BindingStation BindingID: {self.binding_id}, StationID: {self.station_id}>"