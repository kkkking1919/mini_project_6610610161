import os
from typing import Optional
from fastapi import Header
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base

DB_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "fall_detection.db")
TEST_DB_PATH = os.path.join(DB_DIR, "test_fall.db")

engine = create_engine(
    f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

test_engine = create_engine(
    f"sqlite:///{TEST_DB_PATH}", connect_args={"check_same_thread": False}
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

def init_db():
    Base.metadata.create_all(bind=engine)
    Base.metadata.create_all(bind=test_engine)

def get_db(x_test_mode: Optional[str] = Header(None)):
    if x_test_mode and x_test_mode.lower() in ("true", "1", "yes"):
        db = TestSessionLocal()
    else:
        db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
