# bot.py
from __future__ import annotations
import os, logging, datetime as dt, csv, re, time, io
import pytz
from datetime import timezone as _tz
from dotenv import dotenv_values
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Charts
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from db import (
    save_report, list_reports, list_between, list_between_all,
    delete_by_id, delete_last, count_between, delete_between, update_note
)

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("auto_sam")

# ---------- Config / Env ----------
cfg = {**dotenv_values(".env"), **os.environ}
BOT_TOKEN = cfg.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN missing in env")

TZNAME = cfg.get("TIMEZONE", "Asia/Phnom_Penh")
TZ = pytz.timezone(TZNAME)

HELP_TEXT = (
  "Hi! I’m Auto SAM 🤖\n\n"
  "Commands:\n"
  "• /store <text> [#tag]\n"
  "• /sum [daily|weekly|monthly] [#tag]  (or /sum <text> to store+show today)\n"
  "• /list [YYYY-MM-DD] [#tag]\n"
  "• /total [daily|weekly|monthly] [#tag]\n"
  "• /export daily|weekly|monthly [#tag]\n"
  "• /search <keywords> [#tag]\n"
  "• /editlast <new text>\n"
  "• /delete <id|last>\n"
  "• /clear daily|weekly|monthly [#tag] [confirm]\n"
  "• /update <id> <new text>\n"
  "• /breakdown [daily|weekly|monthly]\n"
  "• /sumcats [daily|weekly|monthly]\n"
  "• /sumid <entry_id>\n"
  "• /topcats [daily|weekly|monthly] [group]\n"
  "• /trend <daily|weekly> [group] [#tag]\n"
)

# ---------- Helpers ----------
def _to_local(dt_in: dt.datetime) -> dt.datetime:
    if dt_in.tzinfo is None:
        dt_in = dt_in.replace(tzinfo=_tz.utc)
    return dt_in.astimezone(TZ)

def _range_for_period(period: str) -> tuple[dt.datetime, dt.datetime, str]:
    now_local = dt.datetime.now(TZ)
    if period == "daily":
        start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = now_local.replace(hour=23, minute=59, second=59, microsecond=999999)
        label = now_local.strftime("%Y-%m-%d")
    elif period == "weekly":
        start_local = (now_local - dt.timedelta(days=now_local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = (start_local + dt.timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)
        label = f"{start_local.strftime('%Y-%m-%d')}_to_{end_local.strftime('%Y-%m-%d')}"
    else:  # monthly
        start_local = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start_local.month == 12:
            next_month = start_local.replace(year=start_local.year+1, month=1)
        else:
            next_month = start_local.replace(month=start_local.month+1)
        end_local = (next_month - dt.timedelta(seconds=1)).replace(hour=23, minute=59, second=59, microsecond=999999)
        label = now_local.strftime("%Y-%m")
    return start_local.astimezone(pytz.UTC), end_local.astimezone(pytz.UTC), label

def _parse_free_text_from_msg(msg: str) -> str | None:
    if not msg:
        return None
    t = msg.strip()
    if t.startswith("/"):
        first_sp = t.find(" ")
        if first_sp == -1:
            after = t.split("\n", 1)
            t = after[1].strip() if len(after) > 1 else ""
        else:
            t = t[first_sp+1:].strip()
    return t or None

def _who_from_row(r) -> str:
    return f"@{getattr(r, 'username', '')}" if getattr(r, "username", None) else f"ID:{r.user_id}"

def _who_from_userobj(user) -> str:
    if getattr(user, "username", None):
        return f"@{user.username}"
    if getattr(user, "first_name", None):
        return user.first_name
    return f"ID:{user.id}"

# ---------- Hashtags / categories ----------
TAG_RE = re.compile(r"#(\w+)")
def _first_hashtag(text: str | None) -> str | None:
    if not text:
        return None
    m = TAG_RE.search(text)
    return m.group(1).lower() if m else None

def _extract_tag_from_args(args: list[str]) -> str | None:
    for a in args:
        if a.startswith("#") and len(a) > 1:
            return a[1:].lower()
    return None

# ---------- Amount extraction ----------
_KHMER_DIGITS = str.maketrans("០១២៣៤៥៦៧៨៩", "0123456789") # includes Khmer numerals
def _normalize_digits(text: str) -> str:
    return text.translate(_KHMER_DIGITS)

_AMOUNT_RE = re.compile(r"(?<!\w)([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?!\w)")
POSITIVE_HINTS = {"deposit","income","revenue","sale","sales","add","topup","top-up","ឈ្នះ","បញ្ចូល","ដាក់","ចូល"}
NEGATIVE_HINTS = {"withdraw","expense","cost","bet","pay","paid","payout","minus","ដក","ចេញ","ចំណាយ","ភ្នាល់","បង់"}

def _looks_like_money(raw: str) -> bool:
    no_commas = raw.replace(",", "")
    return ("," in raw) or (len(no_commas.split(".")[0]) >= 4)

def _extract_signed_amounts(note: str) -> list[float]:
    text = _normalize_digits(note or "")
    lowered = text.lower()
    signed: list[float] = []
    neg = any(k in lowered for k in NEGATIVE_HINTS)
    pos = any(k in lowered for k in POSITIVE_HINTS)
    sign = -1.0 if (neg and not pos) else 1.0
    for m in _AMOUNT_RE.finditer(text):
        raw = m.group(1)
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            continue
        if not _looks_like_money(raw) and abs(val) < 1000:
            continue
        signed.append(sign * val)
    return signed

def _sum_rows(rows) -> float:
    return sum(sum(_extract_signed_amounts(r.note or "")) for r in rows)

# ---------- Duplicate protection ----------
DUP_WINDOW_SEC = 15
_DUP_CACHE: dict[str, tuple[str, float]] = {}
def _is_dup(user_id: str, text: str) -> bool:
    now = time.time()
    last = _DUP_CACHE.get(user_id)
    if last and last[0] == text and (now - last[1]) <= DUP_WINDOW_SEC:
        return True
    _DUP_CACHE[user_id] = (text, now)
    return False

# ---------- Auto-tag rules ----------
KEYWORD_TAGS = {
    "salary": "salary", "wage": "salary",
    "deposit": "deposit", "topup": "deposit", "top-up": "deposit",
    "bet": "betting", "betting": "betting", "win": "betting", "lose": "betting",
    "expense": "expense", "pay": "expense", "paid": "expense", "payout": "expense",
}
def _infer_tag_if_missing(text: str, existing: str | None) -> str | None:
    if existing:
        return existing
    t = (text or "").lower()
    for key, tag in KEYWORD_TAGS.items():
        if key in t:
            return tag
    return None

# ---------- Core commands ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text(HELP_TEXT)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text(HELP_TEXT)

async def store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    text = " ".join(context.args).strip() if context.args else _parse_free_text_from_msg(update.message.text or "")
    if not text:
        await update.message.reply_text("Usage: /store <your text> [#tag]")
        return
    user = update.effective_user
    if _is_dup(str(user.id), text):
        await update.message.reply_text("Ignored duplicate message (sent too quickly).")
        return
    cat = _infer_tag_if_missing(text, _first_hashtag(text))
    rec = save_report(
        user_id=str(user.id),
        username=user.username,
        period="note",
        amount=0.0,
        category=cat,
        note=text,
        created_at=dt.datetime.now(_tz.utc),
    )
    await update.message.reply_text(f"✅ Stored (id {rec.id}) by {_who_from_userobj(user)}.")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user = update.effective_user
    date_only = None
    category = _extract_tag_from_args(context.args or [])
    if context.args:
        try:
            date_only = dt.date.fromisoformat(context.args[0])
        except Exception:
            pass
    rows = list_reports(period=None, date_only=date_only, user_id=str(user.id), category=category)
    if not rows:
        hint = (f" on {date_only.isoformat()}" if date_only else "") + (f" for #{category}" if category else "")
        await update.message.reply_text(f"No entries{hint}.")
        return
    rows = sorted(rows, key=lambda r: r.created_at, reverse=True)
    lines = []
    for r in rows[:50]:
        when = _to_local(r.created_at).strftime("%Y-%m-%d %H:%M")
        who = _who_from_row(r)
        text = (r.note or "").strip()
        if len(text) > 200:
            text = text[:200] + "…"
        lines.append(f"- [{r.id}] [{when}] {who}: {text}")
    more = "" if len(rows) <= 50 else f"\n…and {len(rows) - 50} more."
    await update.message.reply_text("\n".join(lines) + more)

async def sum_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user = update.effective_user
    period = "daily"
    category = None
    free_text = None
    for a in (context.args or []):
        la = a.lower()
        if la in ("daily","weekly","monthly"):
            period = la
        elif la.startswith("#"):
            category = la[1:]
        else:
            free_text = " ".join(context.args).strip()
            break
    if free_text:
        if _is_dup(str(user.id), free_text):
            await update.message.reply_text("Ignored duplicate message (sent too quickly).")
        else:
            cat = _infer_tag_if_missing(free_text, _first_hashtag(free_text) or category)
            save_report(
                user_id=str(user.id), username=user.username,
                period="note", amount=0.0, category=cat, note=free_text,
                created_at=dt.datetime.now(_tz.utc),
            )
            await update.message.reply_text(f"✅ Stored by {_who_from_userobj(user)}. Showing today's entries…")
        period = "daily"
    start_utc, end_utc, label = _range_for_period(period)
    rows = list_between(start_utc, end_utc, user_id=str(user.id), category=category)
    if not rows:
        tag_hint = f" • #{category}" if category else ""
        await update.message.reply_text(f"🧾 {period.capitalize()} entries ({label}){tag_hint}\nNo entries.")
        return
    rows = sorted(rows, key=lambda r: r.created_at, reverse=True)
    grand_total = 0.0
    lines = []
    for r in rows[:50]:
        when = _to_local(r.created_at).strftime("%H:%M")
        who = _who_from_row(r)
        text = (r.note or "").strip()
        amts = _extract_signed_amounts(text)
        entry_sum = sum(amts) if amts else 0.0
        grand_total += entry_sum
        if amts:
            nums_str = ", ".join(f"{a:,.0f}" if float(a).is_integer() else f"{a:,.2f}" for a in amts)
            entry_sum_str = f"{entry_sum:,.0f}" if float(entry_sum).is_integer() else f"{entry_sum:,.2f}"
            lines.append(f"• {when} {who}: {text}\n    ↳ numbers: {nums_str} | sum: {entry_sum_str}")
        else:
            lines.append(f"• {when} {who}: {text}")
    more = "" if len(rows) <= 50 else f"\n…and {len(rows) - 50} more."
    grand_str = f"{grand_total:,.0f}" if float(grand_total).is_integer() else f"{grand_total:,.2f}"
    header = f"🧾 {period.capitalize()} entries ({label}) • {_who_from_userobj(user)}"
    if category:
        header += f" • #{category}"
    header += f"\n💰 Total: {grand_str}\n"
    await update.message.reply_text(header + "\n".join(lines) + more)

async def total_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user = update.effective_user
    period = "daily"
    category = _extract_tag_from_args(context.args or [])
    for a in (context.args or []):
        la = a.lower()
        if la in ("daily","weekly","monthly"):
            period = la
    start_utc, end_utc, label = _range_for_period(period)
    rows = list_between(start_utc, end_utc, user_id=str(user.id), category=category)
    total = _sum_rows(rows)
    total_str = f"{total:,.0f}" if float(total).is_integer() else f"{total:,.2f}"
    tag_hint = f" • #{category}" if category else ""
    await update.message.reply_text(f"💰 {period.capitalize()} total ({label}){tag_hint} • {_who_from_userobj(user)}: {total_str}")

async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage: /export <daily|weekly|monthly> [#tag]")
        return
    period = None
    category = None
    for a in context.args:
        la = a.lower()
        if la in ("daily","weekly","monthly"):
            period = la
        elif la.startswith("#"):
            category = la[1:]
    if not period:
        await update.message.reply_text("Usage: /export <daily|weekly|monthly> [#tag]")
        return
    start_utc, end_utc, label = _range_for_period(period)
    rows = list_between(start_utc, end_utc, user_id=str(user.id), category=category)
    if not rows:
        tag_hint = f" for #{category}" if category else ""
        await update.message.reply_text(f"No data for {period} ({label}){tag_hint}.")
        return
    os.makedirs("exports", exist_ok=True)
    tag_suffix = (f"_{category}" if category else "")
    tmp_path = f"exports/{user.id}_{period}_export_{label}{tag_suffix}.csv"
    grand_total = 0.0
    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["created_at_utc", "created_at_local", "username", "category", "text", "numbers_found", "entry_sum"])
        for r in rows:
            numbers = _extract_signed_amounts(r.note or "")
            entry_sum = sum(numbers) if numbers else 0.0
            grand_total += entry_sum
            numbers_str = ", ".join(f"{a:,.0f}" if float(a).is_integer() else f"{a:,.2f}" for a in numbers)
            w.writerow([
                (r.created_at.replace(tzinfo=_tz.utc) if r.created_at.tzinfo is None else r.created_at).isoformat(),
                _to_local(r.created_at).strftime("%Y-%m-%d %H:%M:%S"),
                r.username or "",
                (getattr(r, "category", "") or ""),
                (r.note or "").replace("\n", " ").strip(),
                numbers_str,
                f"{entry_sum:,.0f}" if float(entry_sum).is_integer() else f"{entry_sum:,.2f}",
            ])
        w.writerow([])
        w.writerow(["", "", "", "", "GRAND TOTAL", "", f"{grand_total:,.0f}" if float(grand_total).is_integer() else f"{grand_total:,.2f}"])
    grand_caption = f"{grand_total:,.0f}" if float(grand_total).is_integer() else f"{grand_total:,.2f}"
    tag_hint = f" • #{category}" if category else ""
    await update.message.reply_document(
        document=InputFile(tmp_path, filename=os.path.basename(tmp_path)),
        caption=f"{period.capitalize()} export — {label}{tag_hint} • {_who_from_userobj(user)} • 💰 Total: {grand_caption}"
    )

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user = update.effective_user
    if not context.args:
        return await update.message.reply_text("Usage: /search <keywords> [#tag]")
    args = list(context.args)
    category = None
    if args[-1].startswith("#"):
        category = args[-1][1:].lower()
        args = args[:-1]
    q = " ".join(args).lower().strip()
    rows = list_reports(user_id=str(user.id), category=category)
    hits = [r for r in rows if q in (r.note or "").lower()]
    if not hits:
        tag_hint = f" in #{category}" if category else ""
        return await update.message.reply_text(f"No matches for “{q}”{tag_hint}.")
    lines = []
    for r in hits[:20]:
        when = _to_local(r.created_at).strftime("%Y-%m-%d %H:%M")
        who = _who_from_row(r)
        text = (r.note or "").strip()
        if len(text) > 200: text = text[:200] + "…"
        lines.append(f"- [{r.id}] [{when}] {who}: {text}")
    more = "" if len(hits) <= 20 else f"\n…and {len(hits) - 20} more"
    await update.message.reply_text("\n".join(lines) + more)

async def editlast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user = update.effective_user
    rows = list_reports(user_id=str(user.id))
    if not rows:
        return await update.message.reply_text("No entries.")
    last = sorted(rows, key=lambda r: r.created_at, reverse=True)[0]
    if not context.args:
        return await update.message.reply_text("Usage: /editlast <new text>")
    new_text = " ".join(context.args).strip()
    update_note(last.id, str(user.id), new_text)
    await update.message.reply_text(f"✏️ Updated last entry [{last.id}] • {_who_from_userobj(user)}")

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage: /delete <id|last>")
        return
    arg = context.args[0].strip().lower()
    if arg == "last":
        n = delete_last(str(user.id))
        await update.message.reply_text(("🗑️ Deleted the last entry." if n == 1 else "No entries to delete.") + f" • {_who_from_userobj(user)}")
        return
    try:
        entry_id = int(arg)
    except ValueError:
        await update.message.reply_text("Usage: /delete <id|last>")
        return
    n = delete_by_id(entry_id, str(user.id))
    if n == 1:
        await update.message.reply_text(f"🗑️ Deleted entry id {entry_id} • {_who_from_userobj(user)}.")
    else:
        await update.message.reply_text(f"Entry id {entry_id} not found • {_who_from_userobj(user)}.")

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage: /clear <daily|weekly|monthly> [#tag] [confirm]")
        return
    period = None
    category = None
    confirm = False
    for a in context.args:
        la = a.lower()
        if la in ("daily","weekly","monthly"):
            period = la
        elif la == "confirm":
            confirm = True
        elif la.startswith("#"):
            category = la[1:]
    if not period:
        await update.message.reply_text("Usage: /clear <daily|weekly|monthly> [#tag] [confirm]")
        return
    start_utc, end_utc, label = _range_for_period(period)
    if not confirm:
        n = count_between(start_utc, end_utc, str(user.id), category=category)
        tag_hint = f" for #{category}" if category else ""
        await update.message.reply_text(
            f"⚠️ This will delete {n} entries for {period} ({label}){tag_hint} • {_who_from_userobj(user)}.\n"
            f"Run `/clear {period}{(' #'+category) if category else ''} confirm` to proceed."
        )
        return
    n = delete_between(start_utc, end_utc, str(user.id), category=category)
    tag_hint = f" for #{category}" if category else ""
    await update.message.reply_text(f"🗑️ Deleted {n} entries for {period} ({label}){tag_hint} • {_who_from_userobj(user)}.")

async def update_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user = update.effective_user
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /update <id> <new text>")
        return
    try:
        entry_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Entry ID must be a number.")
        return
    new_text = " ".join(context.args[1:]).strip()
    ok = update_note(entry_id, str(user.id), new_text)
    if ok:
        await update.message.reply_text(f"✏️ Updated entry {entry_id} • {_who_from_userobj(user)}:\n{new_text}")
    else:
        await update.message.reply_text(f"Entry id {entry_id} not found • {_who_from_userobj(user)}.")

async def breakdown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user = update.effective_user
    period = "daily"
    if context.args and context.args[0].lower() in ("daily","weekly","monthly"):
        period = context.args[0].lower()
    start_utc, end_utc, label = _range_for_period(period)
    rows = list_between(start_utc, end_utc, user_id=str(user.id))
    if not rows:
        await update.message.reply_text(f"No entries for {period} ({label}) • {_who_from_userobj(user)}.")
        return
    total = _sum_rows(rows)
    total_str = f"{total:,.0f}" if float(total).is_integer() else f"{total:,.2f}"
    await update.message.reply_text(f"📊 {period.capitalize()} breakdown ({label}) • {_who_from_userobj(user)}\n💰 Total: {total_str}")

async def sumcats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user = update.effective_user
    period = "daily"
    if context.args and context.args[0].lower() in ("daily","weekly","monthly"):
        period = context.args[0].lower()
    start_utc, end_utc, label = _range_for_period(period)
    rows = list_between(start_utc, end_utc, user_id=str(user.id))
    if not rows:
        await update.message.reply_text(f"No entries for {period} ({label}).")
        return
    totals: dict[str, float] = {}
    grand_total = 0.0
    for r in rows:
        cat = (getattr(r, "category", "") or "uncategorized").lower()
        entry_sum = sum(_extract_signed_amounts(r.note or ""))
        totals[cat] = totals.get(cat, 0.0) + entry_sum
        grand_total += entry_sum
    lines = [f"📊 {period.capitalize()} by category ({label})"]
    for cat, total in totals.items():
        total_str = f"{total:,.0f}" if float(total).is_integer() else f"{total:,.2f}"
        lines.append(f"#{cat}: {total_str}")
    grand_str = f"{grand_total:,.0f}" if float(grand_total).is_integer() else f"{grand_total:,.2f}"
    lines.append(f"—\nTotal: {grand_str}")
    await update.message.reply_text("\n".join(lines))

def _format_single_entry_sum(rec) -> str:
    amts = _extract_signed_amounts(rec.note or "")
    entry_sum = sum(amts) if amts else 0.0
    nums_str = ", ".join(f"{a:,.0f}" if float(a).is_integer() else f"{a:,.2f}" for a in amts) if amts else "—"
    entry_sum_str = f"{entry_sum:,.0f}" if float(entry_sum).is_integer() else f"{entry_sum:,.2f}"
    who = _who_from_row(rec)
    when = _to_local(rec.created_at).strftime("%Y-%m-%d %H:%M")
    return (
        f"🧾 Entry [{rec.id}] ({when}) • {who}\n"
        f"Text: {rec.note}\n"
        f"Numbers: {nums_str}\n"
        f"Sum: {entry_sum_str}"
    )

async def sumid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage: /sumid <entry_id>")
        return
    try:
        entry_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Entry ID must be a number.")
        return
    rows = list_reports(user_id=str(user.id))
    rec = next((r for r in rows if r.id == entry_id), None)
    if not rec:
        await update.message.reply_text(f"Entry id {entry_id} not found.")
        return
    await update.message.reply_text(_format_single_entry_sum(rec))

# ---------- Analytics additions ----------
async def topcats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user = update.effective_user
    period = "daily"
    use_group = False
    for a in (context.args or []):
        la = a.lower()
        if la in ("daily","weekly","monthly"):
            period = la
        elif la == "group":
            use_group = True
    start_utc, end_utc, label = _range_for_period(period)
    rows = list_between_all(start_utc, end_utc) if use_group else list_between(start_utc, end_utc, user_id=str(user.id))
    if not rows:
        scope = "group" if use_group else _who_from_userobj(user)
        await update.message.reply_text(f"No entries for {period} ({label}) • {scope}.")
        return
    totals: dict[str, float] = {}
    for r in rows:
        cat = (getattr(r, "category", "") or "uncategorized").lower()
        totals[cat] = totals.get(cat, 0.0) + sum(_extract_signed_amounts(r.note or ""))
    top = sorted(totals.items(), key=lambda kv: abs(kv[1]), reverse=True)[:10]
    lines = [f"🏆 Top categories — {period.capitalize()} ({label}) {'• group' if use_group else '• ' + _who_from_userobj(user)}"]
    for cat, val in top:
        v = f"{val:,.0f}" if float(val).is_integer() else f"{val:,.2f}"
        lines.append(f"#{cat}: {v}")
    await update.message.reply_text("\n".join(lines))

def _send_trend_chart(chat_fn, labels, values, title: str):
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(range(len(values)), values, marker="o")
    ax.set_title(title)
    ax.set_ylabel("Total")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right")
    fig.tight_layout()
    bio = io.BytesIO()
    fig.savefig(bio, format="png", dpi=160)
    plt.close(fig)
    bio.seek(0)
    return chat_fn(document=InputFile(bio, filename="trend.png"))

async def trend_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user = update.effective_user
    mode = "daily"   # daily or weekly
    use_group = False
    category = None
    for a in (context.args or []):
        la = a.lower()
        if la in ("daily","weekly"):
            mode = la
        elif la == "group":
            use_group = True
        elif la.startswith("#"):
            category = la[1:]
    if mode == "daily":
        today = dt.datetime.now(TZ).date()
        days = [today - dt.timedelta(days=i) for i in range(6, -1, -1)]
        labels = [d.strftime("%m-%d") for d in days]
        values = []
        for d in days:
            start_local = dt.datetime.combine(d, dt.time.min).replace(tzinfo=TZ)
            end_local = dt.datetime.combine(d, dt.time.max).replace(tzinfo=TZ)
            start_utc, end_utc = start_local.astimezone(pytz.UTC), end_local.astimezone(pytz.UTC)
            rows = list_between_all(start_utc, end_utc, category=category) if use_group else list_between(start_utc, end_utc, user_id=str(user.id), category=category)
            values.append(_sum_rows(rows))
        title = f"Daily totals (last 7 days){' • #'+category if category else ''}{' • group' if use_group else ''}"
    else:
        now_local = dt.datetime.now(TZ)
        curr_monday = (now_local - dt.timedelta(days=now_local.weekday())).date()
        week_starts = [curr_monday - dt.timedelta(weeks=i) for i in range(7, -1, -1)]
        labels, values = [], []
        for ws in week_starts:
            start_local = dt.datetime.combine(ws, dt.time.min).replace(tzinfo=TZ)
            end_local = (start_local + dt.timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)
            start_utc, end_utc = start_local.astimezone(pytz.UTC), end_local.astimezone(pytz.UTC)
            rows = list_between_all(start_utc, end_utc, category=category) if use_group else list_between(start_utc, end_utc, user_id=str(user.id), category=category)
            values.append(_sum_rows(rows))
            labels.append(ws.strftime("%m-%d"))
        title = f"Weekly totals (last 8 weeks){' • #'+category if category else ''}{' • group' if use_group else ''}"
    await _send_trend_chart(update.message.reply_document, labels, values, title)

# -------- Main --------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("store", store))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("sum", sum_cmd))
    app.add_handler(CommandHandler("total", total_cmd))
    app.add_handler(CommandHandler("export", export_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("editlast", editlast_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CommandHandler("update", update_cmd))
    app.add_handler(CommandHandler("breakdown", breakdown_cmd))
    app.add_handler(CommandHandler("sumcats", sumcats_cmd))
    app.add_handler(CommandHandler("sumid", sumid_cmd))

    # New analytics
    app.add_handler(CommandHandler("topcats", topcats_cmd))
    app.add_handler(CommandHandler("trend", trend_cmd))

    async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message:
            await update.message.reply_text("Unknown command. Try /help")
    app.add_handler(MessageHandler(filters.COMMAND, unknown))

    log.info("Bot started. Polling…")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
