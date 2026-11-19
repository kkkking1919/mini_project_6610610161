from sqlalchemy import Column, Integer, Float, String, DateTime, Boolean, Text
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime, timezone, timedelta

# Bangkok Timezone (UTC+7)
BANGKOK_TZ = timezone(timedelta(hours=7))

def get_bangkok_now_str() -> str:
    return datetime.now(BANGKOK_TZ).strftime("%Y-%m-%d %H:%M:%S")

def get_bangkok_date_str() -> str:
    return datetime.now(BANGKOK_TZ).strftime("%Y-%m-%d")

Base = declarative_base()

class FallEvent(Base):
    __tablename__ = "fall_events"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    timestamp = Column(String, default=get_bangkok_now_str)
    max_g = Column(Float, nullable=False)
    temperature = Column(Float, nullable=True)
    humidity = Column(Float, nullable=True)
    pressure = Column(Float, nullable=True)
    context_risk = Column(String, default="Moderate risk: fall impact detected")
    status = Column(String, default="PENDING")  # PENDING, RESOLVED

class Telemetry(Base):
    __tablename__ = "telemetry"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    timestamp = Column(String, default=get_bangkok_now_str)
    temperature = Column(Float, nullable=True)
    humidity = Column(Float, nullable=True)
    pressure = Column(Float, nullable=True)
    rssi = Column(Integer, nullable=True)
    accel_x = Column(Float, default=0.0)
    accel_y = Column(Float, default=0.0)
    accel_z = Column(Float, default=0.0)
    gyro_x = Column(Float, default=0.0)
    gyro_y = Column(Float, default=0.0)
    gyro_z = Column(Float, default=0.0)

class DailySummary(Base):
    __tablename__ = "daily_summaries"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    date = Column(String, index=True, default=get_bangkok_date_str)
    summary_text = Column(Text, nullable=False)
    activity_score = Column(Integer, default=75)
    falls_count = Column(Integer, default=0)
    created_at = Column(String, default=get_bangkok_now_str)
