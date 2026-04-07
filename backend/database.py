import os
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from dotenv import load_dotenv

# Load env vars
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# For Supabase connection via Session Pooler, we use the DATABASE_URL.
# If connecting from a local machine, we ensure the URL is correct.
if not DATABASE_URL:
    # Fallback to local dev if URL is missing (not expected here)
    DATABASE_URL = "sqlite:///./timesheets.db"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class TimesheetLog(Base):
    __tablename__ = "timesheet_logs"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(50))  # "success", "error"
    file_name = Column(String(255))
    total_pdfs = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)

class TimesheetProgress(Base):
    __tablename__ = "timesheet_progress"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(100), index=True)
    message = Column(Text)
    step = Column(Integer, nullable=True)
    total = Column(Integer, nullable=True)
    status = Column(String(50), default="processing") # processing, done, error
    created_at = Column(DateTime, default=datetime.utcnow)

# Create tables
def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
