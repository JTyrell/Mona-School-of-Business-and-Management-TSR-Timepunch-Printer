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
elif DATABASE_URL.startswith("postgres://"):
    # SQLAlchemy 1.4+ requires 'postgresql://' instead of 'postgres://'
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# SQLite needs connect_args={"check_same_thread": False} if used in multithreaded Fastapi
engine_kwargs = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Flag to disable DB features gracefully if connection fails
_db_available = True

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
    global _db_available
    try:
        # Check connection before creating tables
        with engine.connect() as conn:
            pass
        Base.metadata.create_all(bind=engine)
        _db_available = True
        return True
    except Exception as e:
        print(f"CRITICAL: Database connection failed (IPv4 issue?). {e}")
        _db_available = False
        return False

def is_db_ready():
    return _db_available

def cleanup_old_data(db, hours=24):
    """Delete progress and execution logs older than 'hours'."""
    from datetime import timedelta
    threshold = datetime.utcnow() - timedelta(hours=hours)
    try:
        db.query(TimesheetProgress).filter(TimesheetProgress.created_at < threshold).delete()
        db.query(TimesheetLog).filter(TimesheetLog.created_at < threshold).delete()
        db.commit()
        return True
    except Exception as e:
        print(f"Cleanup failed: {e}")
        return False

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
