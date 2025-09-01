
from __future__ import annotations
import os, csv, io, pytz, datetime as dt
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import dotenv_values
from db import list_between, chats_with_activity_between, totals_between
from typing import Optional, Callable

def ensure_dir(path:str):
    os.makedirs(path, exist_ok=True)

def daterange_for(period: str, tz: pytz.BaseTzInfo) -> tuple[dt.datetime, dt.datetime, str]:
    now = dt.datetime.now(tz)
    if period == "daily":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        label = now.strftime("%Y-%m-%d")
    elif period == "weekly":
        start = (now - dt.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = (start + dt.timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)
        label = f"{start.strftime('%Y-%m-%d')}_to_{end.strftime('%Y-%m-%d')}"
    elif period == "monthly":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            next_month = start.replace(year=start.year+1, month=1)
        else:
            next_month = start.replace(month=start.month+1)
        end = (next_month - dt.timedelta(seconds=1)).replace(hour=23, minute=59, second=59, microsecond=999999)
        label = now.strftime("%Y-%m")
    else:
        raise ValueError("Unknown period")
    return start.astimezone(pytz.UTC), end.astimezone(pytz.UTC), label

def make_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["created_at_utc","chat_id","user_id","username","period","amount","category","note"])
    for r in rows:
        w.writerow([r.created_at.isoformat(), r.chat_id, r.user_id, r.username or "", r.period, f"{float(r.amount or 0):.2f}", r.category or "", r.note or ""])
    return buf.getvalue().encode("utf-8")

def schedule_jobs(app_send_doc: Callable[[str,str,str,int,str], None], app_send_text: Callable[[str,str], None]):
    cfg = {**dotenv_values(".env")}
    tzname = cfg.get("TIMEZONE", "Asia/Phnom_Penh")
    tz = pytz.timezone(tzname)
    os.makedirs("exports", exist_ok=True)

    sched = BackgroundScheduler(timezone=tz)

    def run_and_send(period: str):
        start_utc, end_utc, label = daterange_for(period, tz)
        chat_ids = chats_with_activity_between(start_utc, end_utc)
        for chat_id in chat_ids:
            rows = list_between(start_utc, end_utc, chat_id=chat_id)
            if not rows:
                continue
            csv_bytes = make_csv(rows)
            filename = f"exports/{chat_id}_{period}_report_{label}.csv"
            with open(filename, "wb") as f:
                f.write(csv_bytes)

            totals = totals_between(start_utc, end_utc, chat_id)
            total_sum = totals.get("__total__", 0.0)
            # Build caption & message
            caption = f"{period.capitalize()} report — {label} — {len(rows)} rows — Total: {total_sum:.2f}"
            lines = [f"🧾 {period.capitalize()} Summary ({label})\nTotal: {total_sum:.2f}"]
            for k, v in totals.items():
                if k == "__total__": 
                    continue
                lines.append(f"• {k}: {v:.2f}")
            app_send_doc(chat_id, period, filename, len(rows), caption)
            app_send_text(chat_id, "\n".join(lines))

    # Daily at 23:55
    sched.add_job(lambda: run_and_send("daily"), CronTrigger(hour=23, minute=55))
    # Weekly (Mon) at 23:59
    sched.add_job(lambda: run_and_send("weekly"), CronTrigger(day_of_week="mon", hour=23, minute=59))
    # Monthly (1st) at 00:05
    sched.add_job(lambda: run_and_send("monthly"), CronTrigger(day="1", hour=0, minute=5))

    sched.start()
    return sched
