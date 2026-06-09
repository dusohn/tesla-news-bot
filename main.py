import os
import sys
import re
import json
import time
import datetime
from typing import Dict, List, Any, Optional, Tuple
from urllib.parse import quote
import json
import time
import datetime
import smtplib
from email.mime.text import MIMEText
from typing import Dict, List, Any, Optional, Tuple
from urllib.parse import quote

import requests
import pytz
# -------------------------------
# Environment Variables
# -------------------------------
OPENAI_API_KEY = (os.environ.get("OPENAI_API_KEY") or "").strip()
TELEGRAM_TOKEN = (os.environ.get("TELEGRAM_TOKEN") or "").strip()
CHAT_ID = (os.environ.get("CHAT_ID") or "").strip()
OPENAI_API_KEY = (os.environ.get("OPENAI_API_KEY") or "").strip()
SMTP_HOST = (os.environ.get("SMTP_HOST") or "smtp.gmail.com").strip()
SMTP_PORT = int((os.environ.get("SMTP_PORT") or "587").strip())
SMTP_USER = (os.environ.get("SMTP_USER") or "").strip()
SMTP_PASSWORD = (os.environ.get("SMTP_PASSWORD") or "").strip()
EMAIL_FROM = (os.environ.get("EMAIL_FROM") or SMTP_USER).strip()
EMAIL_TO = (os.environ.get("EMAIL_TO") or "dusohn@gmail.com").strip()

OPENAI_MODEL = (os.environ.get("OPENAI_MODEL") or "gpt-4o-mini").strip()
OPENAI_URL = "https://api.openai.com/v1/responses"

# Finviz anti-bot softening
FINVIZ_SLEEP_SEC = 1.0

OPENAI_MODEL = (os.environ.get("OPENAI_MODEL") or "gpt-4o-mini").strip()
OPENAI_URL = "https://api.openai.com/v1/responses"

# Telegram message hard limit is 4096
TELEGRAM_CHUNK_SIZE = 3800

# Finviz anti-bot softening
FINVIZ_SLEEP_SEC = 1.0

MAG7 = [
    {"name": "Apple", "ticker": "AAPL", "emoji": "?뜋"},
    {"name": "Microsoft", "ticker": "MSFT", "emoji": "?뮲"},
        return "?붿빟 ?앹꽦 ?ㅽ뙣"


# -------------------------------
# Telegram
# -------------------------------
def send_telegram_msg(message: str) -> bool:
    print("Sending Telegram...")
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("??Telegram env vars missing")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    chunks = [message[i : i + TELEGRAM_CHUNK_SIZE] for i in range(0, len(message), TELEGRAM_CHUNK_SIZE)]
    for idx, chunk in enumerate(chunks, start=1):
        payload = {"chat_id": CHAT_ID, "text": chunk, "disable_web_page_preview": False}
        resp = requests.post(url, data=payload, timeout=20)
        if resp.status_code != 200:
            print(f"??Telegram send failed (part {idx}/{len(chunks)}): {resp.text[:400]}")
            return False

    print("??Telegram sent")
    return True
# -------------------------------
# Email
# -------------------------------
def send_email_msg(message: str, subject: str) -> bool:
    print("Sending email...")
    if not SMTP_USER or not SMTP_PASSWORD or not EMAIL_FROM or not EMAIL_TO:
        print("Email env vars missing: SMTP_USER/SMTP_PASSWORD/EMAIL_FROM/EMAIL_TO")
        return False

    msg = MIMEText(message, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(msg)
    except Exception as e:
        print(f"Email send failed: {e}")
        return False

    print(f"Email sent to {EMAIL_TO}")
    return True

def decide_summary_lines(ticker: str, n_headlines: int) -> int:
    """
# -------------------------------
# Main
# -------------------------------
def main() -> int:
    debug_chat_id()
    
    print("OpenAI key set?", bool(OPENAI_API_KEY))
    print("Token set?", bool(TELEGRAM_TOKEN), "ChatID set?", bool(CHAT_ID))
    print("OpenAI model:", OPENAI_MODEL)
def main() -> int:
    print("OpenAI key set?", bool(OPENAI_API_KEY))
    print("SMTP user set?", bool(SMTP_USER), "Email to:", EMAIL_TO)
    print("OpenAI model:", OPENAI_MODEL)

    kst = pytz.timezone("Asia/Seoul")
    today = datetime.datetime.now(kst).strftime("%Y-%m-%d")

    report_text = build_report_text(today)
    ok = send_telegram_msg(report_text)
    return 0 if ok else 1

    report_text = build_report_text(today)
    subject = f"미국주식 데일리 브리핑 {today}"
    ok = send_email_msg(report_text, subject)
    return 0 if ok else 1


if __name__ == "__main__":
