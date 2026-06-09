import os
import sys
import re
import json
import time
import datetime
import smtplib
from email.mime.text import MIMEText
from typing import Dict, List, Any, Optional, Tuple
from urllib.parse import quote

import requests
import pytz
from bs4 import BeautifulSoup

# -------------------------------
# Environment Variables
# -------------------------------
OPENAI_API_KEY = (os.environ.get("OPENAI_API_KEY") or "").strip()
SMTP_HOST = (os.environ.get("SMTP_HOST") or "smtp.gmail.com").strip()
SMTP_PORT = int((os.environ.get("SMTP_PORT") or "587").strip())
SMTP_USERNAME = (os.environ.get("SMTP_USERNAME") or "").strip()
SMTP_PASSWORD = (os.environ.get("SMTP_PASSWORD") or "").strip()
EMAIL_FROM = (os.environ.get("EMAIL_FROM") or SMTP_USERNAME).strip()
EMAIL_TO = (os.environ.get("EMAIL_TO") or "dusohn@gmail.com").strip()

OPENAI_MODEL = (os.environ.get("OPENAI_MODEL") or "gpt-4o-mini").strip()
OPENAI_URL = "https://api.openai.com/v1/responses"

# Finviz anti-bot softening
FINVIZ_SLEEP_SEC = 1.0

MAG7 = [
    {"name": "Apple", "ticker": "AAPL", "emoji": "Apple"},
    {"name": "Microsoft", "ticker": "MSFT", "emoji": "Microsoft"},
    {"name": "Amazon", "ticker": "AMZN", "emoji": "Amazon"},
    {"name": "Alphabet", "ticker": "GOOGL", "emoji": "Alphabet"},
    {"name": "Meta", "ticker": "META", "emoji": "Meta"},
    {"name": "NVIDIA", "ticker": "NVDA", "emoji": "NVIDIA"},
    {"name": "Tesla", "ticker": "TSLA", "emoji": "Tesla"},
]


EARNINGS_KEYWORDS = [
    # 실적/발표/가이던스/컨퍼런스콜
    "earnings", "results", "reports", "reported", "q1", "q2", "q3", "q4",
    "quarter", "fiscal", "fy", "guidance", "outlook", "forecast",
    "eps", "revenue", "sales", "profit", "margin",
    "beat", "miss", "tops", "falls short",
    "conference call", "call transcript",
    "preliminary results", "financial results",
    "estimates", "consensus",
]


def is_earnings_headline(title: str) -> bool:
    t = (title or "").lower()
    return any(k in t for k in EARNINGS_KEYWORDS)

def filter_earnings_only_if_earnings_day(items: List[Dict[str, str]]) -> Tuple[List[Dict[str, str]], bool]:
    """
    최근 24시간 목록에 실적/earnings 관련 헤드라인이 하나라도 있으면
    해당 티커의 실적 관련 기사만 남긴다.
    반환: (filtered_items, earnings_mode_enabled)
    """
    if not items:
        return items, False

    has_earnings = any(is_earnings_headline(it.get("title", "")) for it in items)
    if not has_earnings:
        return items, False

    filtered = [it for it in items if is_earnings_headline(it.get("title", ""))]
    # 너무 빡빡하게 걸러져 0개가 되면 원본을 유지한다.
    if not filtered:
        return items, False

    return filtered, True

# -------------------------------
# OpenAI Responses API helpers
# -------------------------------
def _extract_output_text(res_json: dict) -> str:
    """
    Responses API 응답에서 output_text만 추출한다.
    """
    text_parts = []
    for item in (res_json.get("output") or []):
        for c in (item.get("content") or []):
            if c.get("type") == "output_text" and isinstance(c.get("text"), str):
                text_parts.append(c["text"])
    return "\n".join(t.strip() for t in text_parts if t and t.strip()).strip()


# -------------------------------
# Finviz time parsing (ET -> KST filtering)
# -------------------------------
def _parse_finviz_dt_et(raw: str, now_et: datetime.datetime, last_date_et: Optional[datetime.date]) -> Optional[datetime.datetime]:
    """
    Finviz 뉴스 테이블의 시간 문자열을 US/Eastern aware datetime으로 파싱한다.
    지원 형식:
      - "Feb-03-26 08:35AM"
      - "Today 08:35AM"
      - "08:12AM" (이 경우 last_date_et 필요)
    """
    et = pytz.timezone("US/Eastern")
    s = (raw or "").strip()
    if not s:
        return None

    # Today 08:35AM
    if s.lower().startswith("today"):
        parts = s.split()
        if len(parts) >= 2:
            tstr = parts[-1]
            try:
                t = datetime.datetime.strptime(tstr, "%I:%M%p").time()
                return et.localize(datetime.datetime(now_et.year, now_et.month, now_et.day, t.hour, t.minute))
            except Exception:
                return None
        return None

    # "Feb-03-26 08:35AM"
    try:
        dt = datetime.datetime.strptime(s, "%b-%d-%y %I:%M%p")
        return et.localize(dt)
    except Exception:
        pass

    # "08:12AM" (time only)
    try:
        t = datetime.datetime.strptime(s, "%I:%M%p").time()
        if last_date_et is None:
            return None
        return et.localize(datetime.datetime(last_date_et.year, last_date_et.month, last_date_et.day, t.hour, t.minute))
    except Exception:
        return None


def _norm_title(s: str) -> str:
    """
    중복 기사 병합을 위한 타이틀 정규화.
    """
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[‘’“”]", "'", s)
    s = re.sub(r"[^a-z0-9가-힣\s'\-:,.!?()/%&]", "", s)
    return s


def fetch_finviz_news_with_links_24h(ticker: str, max_items: int = 120) -> List[Dict[str, str]]:
    """
    Finviz quote 페이지 뉴스 테이블에서 title/url/published를 수집하고,
    최근 24시간(KST 기준) 항목만 반환한다.
    """
    url = f"https://finviz.com/quote.ashx?t={quote(ticker)}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
    }

    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "lxml")
    table = soup.find("table", class_="news-table")
    if not table:
        return []

    kst = pytz.timezone("Asia/Seoul")
    et = pytz.timezone("US/Eastern")

    now_kst = datetime.datetime.now(kst)
    now_et = now_kst.astimezone(et)
    cutoff_kst = now_kst - datetime.timedelta(hours=24)

    items: List[Dict[str, str]] = []
    last_date_et: Optional[datetime.date] = None

    for row in table.find_all("tr"):
        tds = row.find_all("td")
        if len(tds) < 2:
            continue

        raw_dt = tds[0].get_text(" ", strip=True)  # "Feb-03-26 08:35AM" or "08:12AM"
        a = tds[1].find("a")
        title = a.get_text(" ", strip=True) if a else tds[1].get_text(" ", strip=True)
        link = (a.get("href", "").strip() if a else "")

        if not title:
            continue

        dt_et = _parse_finviz_dt_et(raw_dt, now_et, last_date_et)
        if dt_et is None:
            continue

        last_date_et = dt_et.date()
        dt_kst = dt_et.astimezone(kst)

        if dt_kst < cutoff_kst:
            continue

        items.append(
            {
                "title": title,
                "url": link,
                "published": raw_dt,
                "published_kst": dt_kst.isoformat(),
            }
        )

        if len(items) >= max_items:
            break

    return items

def fetch_finviz_price_change(ticker: str) -> Tuple[str, str]:
    """
    Finviz quote 페이지에서 Price / Change(%)를 파싱한다.
    반환: (price_str, change_str). 실패 시 ("", "")
    """
    url = f"https://finviz.com/quote.ashx?t={quote(ticker)}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
    }

    try:
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")

        table = soup.find("table", class_="snapshot-table2")
        if not table:
            return "", ""

        tds = [td.get_text(" ", strip=True) for td in table.find_all("td")]
        # snapshot-table2는 보통 [Label, Value, Label, Value, ...] 형태다.
        fields = {}
        for i in range(0, len(tds) - 1, 2):
            label = tds[i]
            value = tds[i + 1]
            if label and value:
                fields[label] = value

        price = (fields.get("Price") or "").strip()
        change = (fields.get("Change") or "").strip()

        # Change는 "+1.23%" 같은 문자열 그대로 사용한다.
        return price, change

    except Exception:
        return "", ""


def dedupe_news(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    제목 기반 중복 병합.
    """
    out: List[Dict[str, str]] = []
    seen = set()
    for it in items:
        key = _norm_title(it.get("title", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def format_price_change_suffix(price: str, change: str) -> str:
    """
    예: price="393.67", change="-4.95%" -> " (393.67, 하락 -4.95%)"
    change가 양수면 상승으로 표시한다.
    """
    p = (price or "").strip()
    c = (change or "").strip()

    if not (p and c):
        return ""

    # change 부호로 상승/하락 판단
    if c.startswith("-"):
        dot = "하락"
    else:
        dot = "상승"

    return f" ({p}, {dot} {c})"

# -------------------------------
# Summarization: 10 lines (+ TSLA 20) from Finviz headlines only
# -------------------------------
def summarize_ticker_lines_from_headlines(
    ticker: str,
    company_name: str,
    news_items: List[Dict[str, str]],
    n_lines: int,
    max_headlines_for_llm: int = 12,
) -> str:
    """
    Finviz에서 수집한 헤드라인 목록만으로 n_lines 줄의 요약을 생성한다.
    원문 링크는 코드에서 별도로 출력할 수 있다.
    """
    if not news_items:
        return "최근 24시간 내 Finviz 기사 없음"

    if not OPENAI_API_KEY:
        return "OPENAI_API_KEY 누락"

    use = news_items[:max_headlines_for_llm]

    headline_lines = []
    for i, it in enumerate(use, start=1):
        title = (it.get("title") or "").strip()
        if title:
            headline_lines.append(f"{ticker} N{i}: {title}")
    headlines_text = "\n".join(headline_lines).strip()
    if not headlines_text:
        return "최근 24시간 내 Finviz 기사 없음"

    prompt = f"""
아래는 Finviz에서 수집한 최근 24시간 이내 뉴스 헤드라인 목록이다.

매우 중요:
- 요약은 오직 {company_name}({ticker})와 직접 관련된 내용만 포함해야 한다.
- 다른 기업, 시장 전체, 정치, 거시경제 관련 내용은 헤드라인에 포함되어 있어도 제외한다.
- {company_name}({ticker})의 실적, 제품, 전략, 주가, 규제, 사업과 직접 관련된 정보만 요약한다.

규칙:
- 아래 헤드라인 목록에 있는 내용만 사용한다.
- {company_name}({ticker})와 직접 관련 없는 헤드라인은 무시한다.
- 중복되는 사건은 하나로 병합한다.
- 정확히 {n_lines}줄로 한국어 요약을 작성한다.
- 각 줄은 한 문장으로 작성한다.
- 번호, 불릿, 이모지, 마크다운, JSON은 사용하지 않는다.
- 추측, 평가, 전망, 일반론은 금지한다.
- 헤드라인에 없는 고유명사, 수치, 날짜, 원인은 새로 만들지 않는다.
- 관련 내용이 부족하면 부족하다고 그대로 적고, 억지로 채우지 않는다.

[헤드라인 목록]
{headlines_text}

요약만 출력:
""".strip()

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_API_KEY}"}
    body = {
        "model": OPENAI_MODEL,
        "input": prompt,
        "text": {"format": {"type": "text"}},
    }

    try:
        r = requests.post(OPENAI_URL, headers=headers, json=body, timeout=75)
        if r.status_code != 200:
            return "요약 생성 실패"

        j = r.json()
        txt = (_extract_output_text(j) or "").strip()
        if not txt:
            return "요약 생성 실패"

        # 줄 수 보정: 많으면 자르고 적으면 그대로 둔다.
        lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
        if len(lines) > n_lines:
            lines = lines[:n_lines]
        return "\n".join(lines) if lines else "요약 생성 실패"

    except Exception:
        return "요약 생성 실패"


# -------------------------------
# Email
# -------------------------------
def send_email_msg(message: str, subject: str) -> bool:
    print("Sending email...")
    if not SMTP_USERNAME or not SMTP_PASSWORD or not EMAIL_FROM or not EMAIL_TO:
        print("Email env vars missing: SMTP_USERNAME/SMTP_PASSWORD/EMAIL_FROM/EMAIL_TO")
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
            smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(msg)
    except Exception as e:
        print(f"Email send failed: {e}")
        return False

    print(f"Email sent to {EMAIL_TO}")
    return True

def decide_summary_lines(ticker: str, n_headlines: int) -> int:
    """
    기본: TSLA=20줄, 나머지=10줄.
    기사 수가 적으면 5줄로 축소한다.
    """
    base = 20 if ticker == "TSLA" else 10
    # 최근 24시간 유효 헤드라인이 5개 이하면 5줄로 요약한다.
    return 5 if n_headlines <= 5 else base

# -------------------------------
# Report builder
# -------------------------------
def build_report_text(today: str) -> str:
    lines: List[str] = []
    lines.append("[미국주식 데일리 브리핑 (Finviz / 최근 24시간)]")
    lines.append(f"날짜: {today}")
    lines.append("")

    for c in MAG7:
        t = c["ticker"]
        name = c["name"]
        emoji = c["emoji"]
    
        # 1) 주가 / 변동률
        price, chg = fetch_finviz_price_change(t)
        time.sleep(FINVIZ_SLEEP_SEC)
        suffix = format_price_change_suffix(price, chg)
    
        # 2) Finviz 뉴스 수집
        try:
            raw = fetch_finviz_news_with_links_24h(t, max_items=120)
            time.sleep(FINVIZ_SLEEP_SEC)
        except Exception as e:
            lines.append(f"{emoji} {t} - {name}{suffix}")
            lines.append("Finviz 수집 실패")
            lines.append(f"에러: {e}")
            lines.append("\n---\n")
            continue

        # 3) 중복 제거
        deduped_all = dedupe_news(raw)

        # 선택: 티커/회사명 필터를 추가하려면 여기에 적용한다.
        # deduped_all = filter_headlines_for_ticker(deduped_all, t, name)

        # 4) 실적 모드: 실적 헤드라인이 있으면 실적 관련만 남길 수 있다.
        #deduped, earnings_mode = filter_earnings_only_if_earnings_day(deduped_all)
        deduped = deduped_all
        earnings_mode = False

        # 5) 기사 수가 적으면 5줄, 아니면 기본(TSLA 20 / others 10)
        n_lines = decide_summary_lines(t, n_headlines=len(deduped))

        # 6) 요약
        summary = summarize_ticker_lines_from_headlines(
            ticker=t,
            company_name=name,
            news_items=deduped,
            n_lines=n_lines,
            max_headlines_for_llm=12,
        )
    
        # 7) 출력
        lines.append(f"{emoji} {t} - {name}{suffix}")
        lines.append(summary)
        lines.append("\n---\n")

    return "\n".join(lines).strip()


# -------------------------------
# Main
# -------------------------------
def main() -> int:
    print("OpenAI key set?", bool(OPENAI_API_KEY))
    print("SMTP user set?", bool(SMTP_USERNAME), "Email to:", EMAIL_TO)
    print("OpenAI model:", OPENAI_MODEL)

    kst = pytz.timezone("Asia/Seoul")
    today = datetime.datetime.now(kst).strftime("%Y-%m-%d")

    report_text = build_report_text(today)
    subject = f"미국주식 데일리 브리핑 {today}"
    ok = send_email_msg(report_text, subject)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
