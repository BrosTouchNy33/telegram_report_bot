from __future__ import annotations

import os
import re
import csv
import logging
import datetime as dt
from typing import Optional, List

import pytz
from datetime import timezone as _tz
from dotenv import load_dotenv
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# per-user DB functions
from db import (
    save_report, list_reports, list_between,
    delete_by_id, delete_last, count_between, delete_between, update_note
)

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("auto_sam")

# ---------- Config / Env ----------
# Load .env ONLY for local development. In production (Railway), runtime envs will be used.
load_dotenv(override=False)

def _get_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "y", "on"}

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("ERROR: TELEGRAM_BOT_TOKEN not set in environment.")

ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")  # optional
TZNAME = os.getenv("TIMEZONE", "Asia/Phnom_Penh")
ENABLE_SCHEDULER = _get_bool("ENABLE_SCHEDULER", False)

try:
    TZ = pytz.timezone(TZNAME)
except Exception:
    TZ = pytz.timezone("Asia/Phnom_Penh")
    log.warning("Invalid TIMEZONE '%s'; falling back to Asia/Phnom_Penh", TZNAME)

log.info(
    "Config loaded | TIMEZONE=%s ENABLE_SCHEDULER=%s ADMIN_CHAT_ID=%s",
    TZ.zone, ENABLE_SCHEDULER, ADMIN_CHAT_ID or "(none)"
)

HELP_TEXT = (
  "Hi! I’m Auto SAM 🤖\n\n"
  "Commands:\n"
  "• /store <text>\n"
  "• /sum [daily|weekly|monthly]  (or /sum <text> to store+show today)\n"
  "• /list [YYYY-MM-DD]\n"
  "• /total [daily|weekly|monthly]\n"
  "• /export daily|weekly|monthly\n"
  "• /delete <id|last>\n"
  "• /clear daily|weekly|monthly [confirm]\n"
  "• /update <id> <new text>\n"
  "• /breakdown [daily|weekly|monthly]\n"
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
            next_month = start_local.replace(year=start_local.year + 1, month=1)
        else:
            next_month = start_local.replace(month=start_local.month + 1)
        end_local = (next_month - dt.timedelta(seconds=1)).replace(hour=23, minute=59, second=0, microsecond=0)
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
            t = t[first_sp + 1 :].strip()
    return t or None

def _who_from_row(r) -> str:
    return f"@{r.username}" if r.username else f"ID:{r.user_id}"

def _who_from_userobj(user) -> str:
    if getattr(user, "username", None):
        return f"@{user.username}"
    if getattr(user, "first_name", None):
        return user.first_name
    return f"ID:{user.id}"

# ---------- Amount extraction ----------
_KHMER_DIGITS = str.maketrans("០១២៣៤៥៦៧៨៩", "0123456789")
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

# ---------- Commands ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    text = " ".join(context.args).strip() if context.args else _parse_free_text_from_msg(update.message.text or "")
    if not text:
        await update.message.reply_text("Usage: /store <your text>")
        return
    user = update.effective_user
    rec = save_report(
        user_id=str(user.id),
        username=user.username,
        period="note",
        amount=0.0,
        category=None,
        note=text,
        created_at=dt.datetime.now(_tz.utc),
    )
    await update.message.reply_text(f"✅ Stored (id {rec.id}) by {_who_from_userobj(user)}.")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    date_only = None
    if context.args:
        try:
            date_only = dt.date.fromisoformat(context.args[0])
        except Exception:
            pass
    rows = list_reports(period=None, date_only=date_only, user_id=str(user.id))
    if not rows:
        await update.message.reply_text("No entries." if not date_only else f"No entries on {date_only.isoformat()}.")
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
    user = update.effective_user
    period = "daily"
    free_text = None
    if context.args:
        first = context.args[0].lower()
        if first in ("daily","weekly","monthly"):
            period = first
        else:
            free_text = " ".join(context.args).strip()
    if free_text:
        save_report(
            user_id=str(user.id),
            username=user.username,
            period="note",
            amount=0.0,
            category=None,
            note=free_text,
            created_at=dt.datetime.now(_tz.utc),
        )
        await update.message.reply_text(f"✅ Stored by {_who_from_userobj(user)}. Showing today's entries…")
        period = "daily"

    start_utc, end_utc, label = _range_for_period(period)
    rows = list_between(start_utc, end_utc, user_id=str(user.id))
    if not rows:
        await update.message.reply_text(f"🧾 {period.capitalize()} entries ({label})\nNo entries.")
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
    header = f"🧾 {period.capitalize()} entries ({label}) • {_who_from_userobj(user)}\n💰 Total: {grand_str}\n"
    await update.message.reply_text(header + "\n".join(lines) + more)

async def total_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    period = "daily"
    if context.args:
        arg = context.args[0].lower()
        if arg in ("daily","weekly","monthly"):
            period = arg
        else:
            await update.message.reply_text("Usage: /total [daily|weekly|monthly]")
            return
    start_utc, end_utc, label = _range_for_period(period)
    rows = list_between(start_utc, end_utc, user_id=str(user.id))
    total = sum(sum(_extract_signed_amounts(r.note or "")) for r in rows)
    total_str = f"{total:,.0f}" if float(total).is_integer() else f"{total:,.2f}"
    await update.message.reply_text(f"💰 {period.capitalize()} total ({label}) • {_who_from_userobj(user)}: {total_str}")

async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args or context.args[0].lower() not in ("daily","weekly","monthly"):
        await update.message.reply_text("Usage: /export <daily|weekly|monthly>")
        return
    period = context.args[0].lower()
    start_utc, end_utc, label = _range_for_period(period)
    rows = list_between(start_utc, end_utc, user_id=str(user.id))
    if not rows:
        await update.message.reply_text(f"No data for {period} ({label}).")
        return
    os.makedirs("exports", exist_ok=True)
    tmp_path = f"exports/{user.id}_{period}_export_{label}.csv"
    grand_total = 0.0
    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["created_at_utc", "created_at_local", "username", "text", "numbers_found", "entry_sum"])
        for r in rows:
            numbers = _extract_signed_amounts(r.note or "")
            entry_sum = sum(numbers) if numbers else 0.0
            grand_total += entry_sum
            numbers_str = ", ".join(f"{a:,.0f}" if float(a).is_integer() else f"{a:,.2f}" for a in numbers)
            w.writerow([
                (r.created_at.replace(tzinfo=_tz.utc) if r.created_at.tzinfo is None else r.created_at).isoformat(),
                _to_local(r.created_at).strftime("%Y-%m-%d %H:%M:%S"),
                r.username or "",
                (r.note or "").replace("\n", " ").strip(),
                numbers_str,
                f"{entry_sum:,.0f}" if float(entry_sum).is_integer() else f"{entry_sum:,.2f}",
            ])
        w.writerow([])
        w.writerow(["", "", "", "GRAND TOTAL", "", f"{grand_total:,.0f}" if float(grand_total).is_integer() else f"{grand_total:,.2f}"])

    grand_caption = f"{grand_total:,.0f}" if float(grand_total).is_integer() else f"{grand_total:,.2f}"
    await update.message.reply_document(
        document=InputFile(tmp_path, filename=os.path.basename(tmp_path)),
        caption=f"{period.capitalize()} export — {label} • {_who_from_userobj(user)} • 💰 Total: {grand_caption}"
    )

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    user = update.effective_user
    if not context.args or context.args[0].lower() not in ("daily","weekly","monthly"):
        await update.message.reply_text("Usage: /clear <daily|weekly|monthly> [confirm]")
        return
    period = context.args[0].lower()
    confirm = (len(context.args) > 1 and context.args[1].lower() == "confirm")
    start_utc, end_utc, label = _range_for_period(period)
    if not confirm:
        n = count_between(start_utc, end_utc, str(user.id))
        await update.message.reply_text(
            f"⚠️ This will delete {n} entries for {period} ({label}) • {_who_from_userobj(user)}.\n"
            f"Run `/clear {period} confirm` to proceed."
        )
        return
    n = delete_between(start_utc, end_utc, str(user.id))
    await update.message.reply_text(f"🗑️ Deleted {n} entries for {period} ({label}) • {_who_from_userobj(user)}.")

async def update_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    user = update.effective_user
    period = "daily"
    if context.args and context.args[0].lower() in ("daily","weekly","monthly"):
        period = context.args[0].lower()
    start_utc, end_utc, label = _range_for_period(period)
    rows = list_between(start_utc, end_utc, user_id=str(user.id))
    if not rows:
        await update.message.reply_text(f"No entries for {period} ({label}) • {_who_from_userobj(user)}.")
        return
    total = sum(sum(_extract_signed_amounts(r.note or "")) for r in rows)
    total_str = f"{total:,.0f}" if float(total).is_integer() else f"{total:,.2f}"
    await update.message.reply_text(f"📊 {period.capitalize()} breakdown ({label}) • {_who_from_userobj(user)}\n💰 Total: {total_str}")

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
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CommandHandler("update", update_cmd))
    app.add_handler(CommandHandler("breakdown", breakdown_cmd))

    async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message:
            await update.message.reply_text("Unknown command. Try /help")
    app.add_handler(MessageHandler(filters.COMMAND, unknown))

    log.info("Bot started. Polling…")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
