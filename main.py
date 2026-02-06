-import os
import sys
import re
import json
import time
import datetime
from typing import Dict, List, Any, Optional, Tuple
from urllib.parse import quote

import requests
import pytz
from bs4 import BeautifulSoup

# -------------------------------
# Environment Variables
# -------------------------------
OPENAI_API_KEY = (os.environ.get("OPENAI_API_KEY") or "").strip()
TELEGRAM_TOKEN = (os.environ.get("TELEGRAM_TOKEN") or "").strip()
CHAT_ID = (os.environ.get("CHAT_ID") or "").strip()

OPENAI_MODEL = (os.environ.get("OPENAI_MODEL") or "gpt-4o-mini").strip()
OPENAI_URL = "https://api.openai.com/v1/responses"

# Telegram message hard limit is 4096
TELEGRAM_CHUNK_SIZE = 3800

# Finviz anti-bot softening
FINVIZ_SLEEP_SEC = 1.0

MAG7 = [
    {"name": "Apple", "ticker": "AAPL", "emoji": "🍎"},
    {"name": "Microsoft", "ticker": "MSFT", "emoji": "💻"},
    {"name": "Amazon", "ticker": "AMZN", "emoji": "📦"},
    {"name": "Alphabet", "ticker": "GOOGL", "emoji": "🔍"},
    {"name": "Meta", "ticker": "META", "emoji": "🧠"},
    {"name": "NVIDIA", "ticker": "NVDA", "emoji": "🤖"},
    {"name": "Tesla", "ticker": "TSLA", "emoji": "🚗"},
]


# -------------------------------
# OpenAI Responses API helpers
# -------------------------------
def _extract_output_text(res_json: dict) -> str:
    """
    Responses API 응답에서 output_text만 합쳐 추출
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
    Finviz 뉴스 테이블의 시간 문자열을 US/Eastern aware datetime으로 파싱.
    지원 예:
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
    중복 기사 병합용 타이틀 정규화
    """
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[’‘´`]", "'", s)
    s = re.sub(r"[^a-z0-9가-힣\s'\-:,.!?()/%&]", "", s)
    return s


def fetch_finviz_news_with_links_24h(ticker: str, max_items: int = 120) -> List[Dict[str, str]]:
    """
    Finviz quote 페이지 뉴스 테이블에서 title/url/published를 수집하고,
    최근 24시간(KST 기준)만 남긴 리스트 반환.
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


def dedupe_news(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    제목 기반 중복 병합
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
    Finviz에서 수집한 '헤드라인 목록'만으로 n_lines 줄 한글 요약 생성.
    (원문 링크는 코드에서 별도로 출력)
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
Finviz에서 최근 24시간 내 {company_name}({ticker}) 관련 '헤드라인 목록'이 아래에 주어진다.
너는 이 목록에 있는 내용만 사용해 요약해야 한다.

규칙:
- 아래 목록에 없는 내용/배경지식/추측/일반론 절대 금지
- 중복 헤드라인은 같은 사건이면 하나로 병합하여 요약
- 정확히 {n_lines}줄로 한글 요약
- 각 줄은 독립적인 한 문장
- 번호/불릿/이모지/마크다운/JSON 금지 (줄바꿈만)
- 회사·인물·기관명은 가능한 한 원문 표기를 유지해도 됨

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

        # 줄 수 보정: 많으면 자르고, 적으면 그대로(환각 방지)
        lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
        if len(lines) > n_lines:
            lines = lines[:n_lines]
        return "\n".join(lines) if lines else "요약 생성 실패"

    except Exception:
        return "요약 생성 실패"


# -------------------------------
# Telegram
# -------------------------------
def send_telegram_msg(message: str) -> bool:
    print("Sending Telegram...")
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("❌ Telegram env vars missing")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    chunks = [message[i : i + TELEGRAM_CHUNK_SIZE] for i in range(0, len(message), TELEGRAM_CHUNK_SIZE)]
    for idx, chunk in enumerate(chunks, start=1):
        payload = {"chat_id": CHAT_ID, "text": chunk, "disable_web_page_preview": False}
        resp = requests.post(url, data=payload, timeout=20)
        if resp.status_code != 200:
            print(f"❌ Telegram send failed (part {idx}/{len(chunks)}): {resp.text[:400]}")
            return False

    print("✅ Telegram sent")
    return True


# -------------------------------
# Report builder
# -------------------------------
def build_report_text(today: str) -> str:
    lines: List[str] = []
    lines.append("🧠 [미국주식 데일리 브리핑 (Finviz / 최근 24시간)]")
    lines.append(f"📅 {today}")
    lines.append("")

    for c in MAG7:
        t = c["ticker"]
        name = c["name"]
        emoji = c["emoji"]

        n_lines = 20 if t == "TSLA" else 10

        try:
            raw = fetch_finviz_news_with_links_24h(t, max_items=120)
            time.sleep(FINVIZ_SLEEP_SEC)
        except Exception as e:
            lines.append(f"{emoji} {t} — {name}")
            lines.append("Finviz 수집 실패")
            lines.append(f"에러: {e}")
            lines.append("\n---\n")
            continue

        deduped = dedupe_news(raw)

        summary = summarize_ticker_lines_from_headlines(
            ticker=t,
            company_name=name,
            news_items=deduped,
            n_lines=n_lines,
            max_headlines_for_llm=12,
        )

        lines.append(f"{emoji} {t} — {name}")
        lines.append(summary)

        # 원문 링크: 상위 5개
        link_items: List[Tuple[str, str]] = []
        for it in deduped[:5]:
            title = (it.get("title") or "").strip()
            url = (it.get("url") or "").strip()
            if title and url:
                link_items.append((title, url))

        if link_items:
            lines.append("")
            lines.append("원문 링크")
            for title, url in link_items:
                lines.append(f"- {title}")
                lines.append(f"  {url}")

        lines.append("\n---\n")

    return "\n".join(lines).strip()


# -------------------------------
# Main
# -------------------------------
def main() -> int:
    print("OpenAI key set?", bool(OPENAI_API_KEY))
    print("Token set?", bool(TELEGRAM_TOKEN), "ChatID set?", bool(CHAT_ID))
    print("OpenAI model:", OPENAI_MODEL)

    kst = pytz.timezone("Asia/Seoul")
    today = datetime.datetime.now(kst).strftime("%Y-%m-%d")

    report_text = build_report_text(today)
    ok = send_telegram_msg(report_text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
