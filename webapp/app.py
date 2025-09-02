# webapp/app.py
from __future__ import annotations

import sys, os, glob, csv, datetime as dt
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from functools import wraps
from typing import Optional, Dict

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, send_file, jsonify, abort
)
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from db import Report, Base  # ensure models are loaded

DB_DIR = os.getenv("DB_DIR", "db")           # used only for SQLite fallback
DATABASE_URL = os.getenv("DATABASE_URL")     # set on Railway for shared Postgres
USING_PG = bool(DATABASE_URL)

if USING_PG:
    ENGINE = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
    SessionLocal = sessionmaker(bind=ENGINE, future=True, expire_on_commit=False)

def _global_session():
    return SessionLocal() if USING_PG else None

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "dev-secret-change-me")

ADMIN_USER = os.getenv("ADMIN_DASH_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_DASH_PASS", "change-me")

# ---------- Auth ----------
def login_required(f):
    @wraps(f)
    def _wrap(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return _wrap

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username", "")
        p = request.form.get("password", "")
        if u == ADMIN_USER and p == ADMIN_PASS:
            session["logged_in"] = True
            return redirect(request.args.get("next") or url_for("dashboard"))
        return render_template("login.html", error="Invalid credentials")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ---------- SQLite helpers (fallback only) ----------
def _db_files():
    if USING_PG:
        # not used in Postgres mode
        return []
    os.makedirs(DB_DIR, exist_ok=True)
    for path in glob.glob(os.path.join(DB_DIR, "user_*.db")):
        user_id = os.path.splitext(os.path.basename(path))[0].replace("user_", "")
        yield user_id, path

def _session_for(path: str):
    engine = create_engine(f"sqlite:///{path}", future=True)
    SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    return SessionLocal()

def _date_parse(s: Optional[str]) -> Optional[dt.datetime]:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s)
    except Exception:
        return None

def _apply_filters(q, start_dt: Optional[dt.datetime], end_dt: Optional[dt.datetime], category: Optional[str]):
    if start_dt: q = q.filter(Report.created_at >= start_dt)
    if end_dt:   q = q.filter(Report.created_at <= end_dt)
    if category: q = q.filter(func.lower(Report.category) == category.lower())
    return q

# ---------- Debug ----------
@app.get("/__debug/dbs")
@login_required
def dbg_dbs():
    if USING_PG:
        # Show distinct users in Postgres mode
        with _global_session() as s:
            users = [row[0] for row in s.query(Report.user_id).distinct().all()]
        return jsonify({"using": "postgres", "users": users})
    return jsonify(sorted([p for _, p in _db_files()]))

@app.get("/__debug/env")
@login_required
def dbg_env():
    return jsonify({"DB_DIR": DB_DIR, "USING_PG": USING_PG})

# ---------- Pages ----------
@app.route("/")
@login_required
def dashboard():
    # default window: last 7 days
    now = dt.datetime.utcnow().replace(second=0, microsecond=0)
    start = (now - dt.timedelta(days=7)).replace(hour=0, minute=0)

    def fmt_local(d: dt.datetime) -> str:
        return d.strftime("%Y-%m-%dT%H:%M")

    return render_template(
        "index.html",
        default_start_iso=start.isoformat(),
        default_end_iso=now.isoformat(),
        default_start_local=fmt_local(start),
        default_end_local=fmt_local(now),
    )

# ---------- APIs ----------
@app.get("/api/users")
@login_required
def api_users():
    if USING_PG:
        with _global_session() as s:
            # latest username per user_id (via max(created_at))
            sub = (
                s.query(Report.user_id, func.max(Report.created_at).label("mx"))
                 .group_by(Report.user_id)
                 .subquery()
            )
            rows = (
                s.query(Report.user_id, Report.username)
                 .join(sub, (Report.user_id == sub.c.user_id) & (Report.created_at == sub.c.mx))
                 .all()
            )
        users = [{"id": uid, "name": f"@{uname}" if uname else uid} for uid, uname in rows]
        users.sort(key=lambda u: u["name"].lower())
        return jsonify(users)

    # SQLite fallback
    users = []
    for uid, path in _db_files():
        display = uid
        with _session_for(path) as s:
            row = (
                s.query(Report.username)
                 .filter(Report.username != None)
                 .order_by(Report.created_at.desc())
                 .first()
            )
            if row and row[0]:
                display = f"@{row[0]}"
        users.append({"id": uid, "name": display})
    users.sort(key=lambda u: u["name"].lower())
    return jsonify(users)

@app.get("/api/summary/day_fast")
@login_required
def api_summary_day_fast():
    user_id = request.args.get("user") or None
    start = _date_parse(request.args.get("start"))
    end   = _date_parse(request.args.get("end"))
    category = request.args.get("category") or None

    if USING_PG:
        buckets: Dict[str, float] = {}
        with _global_session() as s:
            q = s.query(func.date(Report.created_at), func.sum(Report.amount))
            if user_id: q = q.filter(Report.user_id == user_id)
            if start:   q = q.filter(Report.created_at >= start)
            if end:     q = q.filter(Report.created_at <= end)
            if category:q = q.filter(func.lower(Report.category) == category.lower())
            for day, total in q.group_by(func.date(Report.created_at)).all():
                buckets[str(day)] = float(total or 0.0)
        labels = sorted(buckets.keys())
        values = [buckets[d] for d in labels]
        return jsonify({"labels": labels, "values": values})

    # SQLite fallback
    buckets: Dict[str, float] = {}
    for uid, path in _db_files():
        if user_id and uid != user_id:
            continue
        with _session_for(path) as s:
            q = s.query(func.date(Report.created_at), func.sum(Report.amount))
            q = _apply_filters(q, start, end, category).group_by(func.date(Report.created_at))
            for day, total in q.all():
                buckets[day] = buckets.get(day, 0.0) + float(total or 0)
    labels = sorted(buckets.keys())
    values = [buckets[d] for d in labels]
    return jsonify({"labels": labels, "values": values})

@app.get("/api/summary/topcats_fast")
@login_required
def api_summary_topcats_fast():
    user_id = request.args.get("user") or None
    start = _date_parse(request.args.get("start"))
    end   = _date_parse(request.args.get("end"))
    category = request.args.get("category") or None

    if USING_PG:
        buckets: Dict[str, float] = {}
        with _global_session() as s:
            q = s.query(
                func.coalesce(func.lower(Report.category), "uncategorized"),
                func.sum(Report.amount)
            )
            if user_id: q = q.filter(Report.user_id == user_id)
            if start:   q = q.filter(Report.created_at >= start)
            if end:     q = q.filter(Report.created_at <= end)
            if category:q = q.filter(func.lower(Report.category) == category.lower())
            for cat, total in q.group_by(func.coalesce(func.lower(Report.category), "uncategorized")).all():
                buckets[cat] = float(total or 0.0)
        labels = sorted(buckets.keys(), key=lambda k: buckets[k], reverse=True)[:12]
        values = [buckets[k] for k in labels]
        return jsonify({"labels": labels, "values": values})

    # SQLite fallback
    buckets: Dict[str, float] = {}
    for uid, path in _db_files():
        if user_id and uid != user_id:
            continue
        with _session_for(path) as s:
            q = s.query(
                func.coalesce(func.lower(Report.category), "uncategorized"),
                func.sum(Report.amount)
            )
            q = _apply_filters(q, start, end, category).group_by(
                func.coalesce(func.lower(Report.category), "uncategorized")
            )
            for cat, total in q.all():
                buckets[cat] = buckets.get(cat, 0.0) + float(total or 0.0)
    labels = sorted(buckets.keys(), key=lambda k: buckets[k], reverse=True)[:12]
    values = [buckets[k] for k in labels]
    return jsonify({"labels": labels, "values": values})

@app.get("/api/reports_table")
@login_required
def api_reports_table():
    """
    Returns:
      { rows: [{user_id, user_name, id, when_iso, category, note, amount}],
        page, page_size, total }
    """
    user_id = request.args.get("user") or None
    start = _date_parse(request.args.get("start"))
    end   = _date_parse(request.args.get("end"))
    category = request.args.get("category") or None
    page = max(int(request.args.get("page", 1)), 1)
    page_size = min(max(int(request.args.get("page_size", 20)), 1), 200)

    if USING_PG:
        with _global_session() as s:
            base = s.query(Report).order_by(Report.created_at.desc())
            if user_id: base = base.filter(Report.user_id == user_id)
            if start:   base = base.filter(Report.created_at >= start)
            if end:     base = base.filter(Report.created_at <= end)
            if category:base = base.filter(func.lower(Report.category) == category.lower())
            total = base.count()
            rows = []
            for r in base.offset((page-1)*page_size).limit(page_size).all():
                uid = r.user_id
                rows.append({
                    "user_id": uid,
                    "user_name": f"@{r.username}" if r.username else uid,
                    "id": r.id,
                    "when_iso": (r.created_at.isoformat() if r.created_at else None),
                    "category": r.category or "",
                    "note": (r.note or "").strip(),
                    "amount": float(r.amount or 0.0),
                })
        return jsonify({"rows": rows, "page": page, "page_size": page_size, "total": total})

    # SQLite fallback
    rows = []
    total = 0
    for uid, path in _db_files():
        if user_id and uid != user_id:
            continue
        with _session_for(path) as s:
            base = s.query(Report).order_by(Report.created_at.desc())
            base = _apply_filters(base, start, end, category)

            total += base.count()

            offset = (page - 1) * page_size
            for r in base.offset(offset).limit(page_size).all():
                rows.append({
                    "user_id": uid,
                    "user_name": f"@{r.username}" if r.username else uid,
                    "id": r.id,
                    "when_iso": (r.created_at.isoformat() if r.created_at else None),
                    "category": r.category or "",
                    "note": (r.note or "").strip(),
                    "amount": float(r.amount or 0.0),
                })
    rows.sort(key=lambda x: x["when_iso"] or "", reverse=True)
    return jsonify({"rows": rows[:page_size], "page": page, "page_size": page_size, "total": total})

# Inline edit & delete
@app.post("/api/report/update")
@login_required
def api_update():
    uid = request.form.get("user_id") or ""
    entry_id = request.form.get("entry_id")
    note = (request.form.get("note") or "").strip()
    amount = request.form.get("amount")
    if not uid or not entry_id:
        abort(400)

    if USING_PG:
        with _global_session() as s:
            row = s.query(Report).filter(Report.id == int(entry_id), Report.user_id == uid).first()
            if not row:
                abort(404)
            row.note = note
            if amount is not None:
                try:
                    row.amount = float(amount)
                except Exception:
                    pass
            s.commit()
        return jsonify({"ok": True})

    # SQLite fallback
    path = os.path.join(DB_DIR, f"user_{uid}.db")
    if not os.path.exists(path):
        abort(404)
    with _session_for(path) as s:
        row = s.query(Report).filter(Report.id == int(entry_id)).first()
        if not row:
            abort(404)
        row.note = note
        if amount is not None:
            try:
                row.amount = float(amount)
            except Exception:
                pass
        s.commit()
    return jsonify({"ok": True})

@app.post("/api/report/delete")
@login_required
def api_delete():
    uid = request.form.get("user_id") or ""
    entry_id = request.form.get("entry_id")
    if not uid or not entry_id:
        abort(400)

    if USING_PG:
        with _global_session() as s:
            row = s.query(Report).filter(Report.id == int(entry_id), Report.user_id == uid).first()
            if not row:
                abort(404)
            s.delete(row)
            s.commit()
        return jsonify({"ok": True})

    # SQLite fallback
    path = os.path.join(DB_DIR, f"user_{uid}.db")
    if not os.path.exists(path):
        abort(404)
    with _session_for(path) as s:
        row = s.query(Report).filter(Report.id == int(entry_id)).first()
        if not row:
            abort(404)
        s.delete(row)
        s.commit()
    return jsonify({"ok": True})

@app.get("/health")
def health():
    return "ok", 200

# CSV export (honors filters)
@app.get("/export.csv")
@login_required
def export_csv():
    user_id = request.args.get("user") or None
    start = _date_parse(request.args.get("start"))
    end   = _date_parse(request.args.get("end"))
    category = request.args.get("category") or None

    tmp = "web_export.csv"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["user_id", "username", "entry_id", "category", "amount", "note", "created_at"])

        if USING_PG:
            with _global_session() as s:
                q = s.query(Report).order_by(Report.created_at.desc())
                if user_id: q = q.filter(Report.user_id == user_id)
                if start:   q = q.filter(Report.created_at >= start)
                if end:     q = q.filter(Report.created_at <= end)
                if category:q = q.filter(func.lower(Report.category) == category.lower())
                for r in q.all():
                    w.writerow([
                        r.user_id, r.username or "", r.id, r.category or "",
                        float(r.amount or 0.0),
                        (r.note or "").replace("\n", " "),
                        r.created_at.isoformat() if r.created_at else ""
                    ])
        else:
            # SQLite fallback
            for uid, path in _db_files():
                if user_id and uid != user_id:
                    continue
                with _session_for(path) as s:
                    q = s.query(Report).order_by(Report.created_at.desc())
                    q = _apply_filters(q, start, end, category)
                    for r in q.all():
                        w.writerow([
                            uid, r.username or "", r.id, r.category or "",
                            float(r.amount or 0.0),
                            (r.note or "").replace("\n", " "),
                            r.created_at.isoformat() if r.created_at else ""
                        ])
    return send_file(tmp, as_attachment=True, download_name="export.csv")

if __name__ == "__main__":
    host = os.getenv("WEB_HOST", "0.0.0.0")
    port = int(os.getenv("PORT", os.getenv("WEB_PORT", "8080")))
    app.run(host=host, port=port, debug=False)
