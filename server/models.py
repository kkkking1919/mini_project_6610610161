from sqlalchemy import Column, Integer, Float, String, DateTime, Boolean, Text
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()

class FallEvent(Base):
    __tablename__ = "fall_events"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    timestamp = Column(String, default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    max_g = Column(Float, nullable=False)
    temperature = Column(Float, nullable=True)
    humidity = Column(Float, nullable=True)
    pressure = Column(Float, nullable=True)
    context_risk = Column(String, default="ปานกลาง")
    status = Column(String, default="PENDING")  # PENDING, RESOLVED

class Telemetry(Base):
    __tablename__ = "telemetry"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    timestamp = Column(String, default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
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
    date = Column(String, index=True)
    summary_text = Column(Text, nullable=False)
    activity_score = Column(Integer, default=75)
    falls_count = Column(Integer, default=0)
    created_at = Column(String, default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
