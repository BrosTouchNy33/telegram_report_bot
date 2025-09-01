# Auto SAM Reports Bot 🤖

A Telegram bot for personal finance / activity logging.  
Built with **python-telegram-bot** and **SQLAlchemy**, each user has their own local database.

---

## 🚀 Features
- `/store <text>` → Save any note or transaction
- `/list [YYYY-MM-DD]` → Show stored entries
- `/sum [daily|weekly|monthly]` → Show entries + auto-sum numbers
- `/total [daily|weekly|monthly]` → Show only total numbers
- `/export <daily|weekly|monthly>` → Export to CSV
- `/delete <id|last>` → Delete entries
- `/update <id> <new text>` → Edit an entry
- `/clear <period> confirm` → Bulk delete (daily/weekly/monthly)
- `/breakdown <period>` → See total breakdown
- Auto-detect **Khmer digits (០១២...)** and numbers with hints like deposit/withdraw

---

## 🛠 Installation

Clone and install dependencies:

```bash
git clone https://github.com/YOUR-USERNAME/telegram_report_bot.git
cd telegram_report_bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
