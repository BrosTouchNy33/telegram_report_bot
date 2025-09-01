from __future__ import annotations
import datetime as dt
from datetime import timezone as _tz
from typing import Optional, List
import os
import glob

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Numeric
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()
DB_DIR = os.getenv("DB_DIR", "db")  

def _db_path_for_user(user_id: str) -> str:
    """Return absolute path for a given user’s SQLite DB file"""
    os.makedirs(DB_DIR, exist_ok=True)
    return os.path.join(DB_DIR, f"user_{user_id}.db")
class Report(Base):
    __tablename__ = "reports"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), index=True, nullable=False)
    username = Column(String(255))
    period = Column(String(32), index=True, nullable=False, default="note")
    amount = Column(Numeric(18, 2), default=0)
    category = Column(String(64), index=True)
    note = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: dt.datetime.now(_tz.utc))

# -------- internal helpers --------
def _db_path_for_user(user_id: str) -> str:
    os.makedirs("db", exist_ok=True)
    return f"db/user_{user_id}.db"

def _engine_for_path(path: str):
    return create_engine(f"sqlite:///{path}", future=True)

def _get_session(user_id: str):
    db_path = _db_path_for_user(user_id)
    engine = _engine_for_path(db_path)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    return SessionLocal()

def _iter_all_user_db_paths() -> list[str]:
    os.makedirs("db", exist_ok=True)
    return sorted(glob.glob("db/user_*.db"))

def _get_session_for_path(path: str):
    engine = _engine_for_path(path)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    return SessionLocal()

# -------- CRUD functions --------
def save_report(
    user_id: str,
    username: Optional[str],
    period: str = "note",
    amount: float = 0.0,
    category: Optional[str] = None,
    note: Optional[str] = None,
    created_at: Optional[dt.datetime] = None,
) -> Report:
    if created_at is None:
        created_at = dt.datetime.now(_tz.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=_tz.utc)
    with _get_session(user_id) as s:
        r = Report(
            user_id=str(user_id),
            username=username,
            period=(period or "note").lower(),
            amount=amount,
            category=(category.lower() if category else None),
            note=note,
            created_at=created_at,
        )
        s.add(r)
        s.commit()
        s.refresh(r)
        return r

def list_reports(
    period: Optional[str] = None,
    date_only: Optional[dt.date] = None,
    user_id: Optional[str] = None,
    category: Optional[str] = None,
) -> List[Report]:
    if not user_id:
        return []
    with _get_session(user_id) as s:
        q = s.query(Report).order_by(Report.created_at.desc())
        if period:
            q = q.filter(Report.period == period.lower())
        if category:
            q = q.filter(Report.category == category.lower())
        if date_only:
            start = dt.datetime.combine(date_only, dt.time.min).replace(tzinfo=_tz.utc)
            end = dt.datetime.combine(date_only, dt.time.max).replace(tzinfo=_tz.utc)
            q = q.filter(Report.created_at >= start, Report.created_at <= end)
        return q.all()

def list_between(
    start_dt: dt.datetime,
    end_dt: dt.datetime,
    user_id: Optional[str] = None,
    period: Optional[str] = None,
    category: Optional[str] = None,
) -> List[Report]:
    if not user_id:
        return []
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=_tz.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=_tz.utc)
    with _get_session(user_id) as s:
        q = s.query(Report).filter(Report.created_at >= start_dt, Report.created_at <= end_dt)
        if period:
            q = q.filter(Report.period == period.lower())
        if category:
            q = q.filter(Report.category == category.lower())
        q = q.order_by(Report.created_at.asc())
        return q.all()

def list_between_all(
    start_dt: dt.datetime,
    end_dt: dt.datetime,
    period: Optional[str] = None,
    category: Optional[str] = None,
) -> List[Report]:
    """Aggregate across ALL user db files."""
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=_tz.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=_tz.utc)

    all_rows: list[Report] = []
    for path in _iter_all_user_db_paths():
        with _get_session_for_path(path) as s:
            q = s.query(Report).filter(Report.created_at >= start_dt, Report.created_at <= end_dt)
            if period:
                q = q.filter(Report.period == period.lower())
            if category:
                q = q.filter(Report.category == category.lower())
            q = q.order_by(Report.created_at.asc())
            all_rows.extend(q.all())
    return all_rows

def delete_by_id(entry_id: int, user_id: str) -> int:
    with _get_session(user_id) as s:
        r = s.query(Report).filter(Report.id == int(entry_id)).first()
        if not r:
            return 0
        s.delete(r)
        s.commit()
        return 1

def delete_last(user_id: str) -> int:
    with _get_session(user_id) as s:
        r = s.query(Report).order_by(Report.created_at.desc()).first()
        if not r:
            return 0
        s.delete(r)
        s.commit()
        return 1

def count_between(start_dt: dt.datetime, end_dt: dt.datetime, user_id: str, category: Optional[str] = None) -> int:
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=_tz.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=_tz.utc)
    with _get_session(user_id) as s:
        q = s.query(Report).filter(Report.created_at >= start_dt, Report.created_at <= end_dt)
        if category:
            q = q.filter(Report.category == category.lower())
        return q.count()

def delete_between(start_dt: dt.datetime, end_dt: dt.datetime, user_id: str, category: Optional[str] = None) -> int:
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=_tz.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=_tz.utc)
    with _get_session(user_id) as s:
        q = s.query(Report).filter(Report.created_at >= start_dt, Report.created_at <= end_dt)
        if category:
            q = q.filter(Report.category == category.lower())
        rows = q.all()
        n = len(rows)
        for r in rows:
            s.delete(r)
        s.commit()
        return n

def update_note(entry_id: int, user_id: str, new_text: str) -> bool:
    with _get_session(user_id) as s:
        r = s.query(Report).filter(Report.id == int(entry_id)).first()
        if not r:
            return False
        r.note = new_text
        s.commit()
        return True
