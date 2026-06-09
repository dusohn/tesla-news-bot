import os
import sys
import re
import json
import time
import datetime
import smtplib
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from email.mime.text import MIMEText
from typing import Dict, List, Any, Optional, Tuple
from urllib.parse import quote, urljoin

import requests
import pytz
from bs4 import BeautifulSoup

# -------------------------------
# Environment Variables
# -------------------------------
OPENAI_API_KEY = (os.environ.get("OPENAI_API_KEY") or "").strip()
SMTP_HOST = (os.environ.get("SMTP_HOST") or "smtp.gmail.com").strip()
SMTP_PORT = int((os.environ.get("SMTP_PORT") or "587").strip())
SMTP_USER = (os.environ.get("SMTP_USER") or "").strip()
SMTP_PASSWORD = (os.environ.get("SMTP_PASSWORD") or "").strip()
EMAIL_FROM = (os.environ.get("EMAIL_FROM") or SMTP_USER).strip()
EMAIL_TO = (os.environ.get("EMAIL_TO") or "dusohn@gmail.com").strip()

OPENAI_MODEL = (os.environ.get("OPENAI_MODEL") or "gpt-5.5").strip()
OPENAI_URL = "https://api.openai.com/v1/responses"

# Finviz anti-bot softening
FINVIZ_SLEEP_SEC = 1.0
NEWS_SLEEP_SEC = 0.35
MAX_ARTICLES_PER_TICKER = 20
ARTICLE_CHAR_LIMIT = int((os.environ.get("ARTICLE_CHAR_LIMIT") or "5000").strip())
LLM_INPUT_CHAR_LIMIT = int((os.environ.get("LLM_INPUT_CHAR_LIMIT") or "70000").strip())

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

MAG7 = [
    {"name": "Alphabet", "ticker": "GOOGL", "emoji": "Alphabet"},
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

    r = requests.get(url, headers=REQUEST_HEADERS, timeout=20)
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
        link = urljoin("https://finviz.com/", a.get("href", "").strip()) if a else ""

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
                "source": "Finviz",
            }
        )

        if len(items) >= max_items:
            break

    return items


def fetch_yahoo_finance_news_24h(ticker: str, max_items: int = 80) -> List[Dict[str, str]]:
    """
    Yahoo Finance RSS에서 최근 24시간(KST 기준) 뉴스 title/url/published를 수집한다.
    """
    rss_url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={quote(ticker)}&region=US&lang=en-US"
    r = requests.get(rss_url, headers=REQUEST_HEADERS, timeout=20)
    r.raise_for_status()

    kst = pytz.timezone("Asia/Seoul")
    now_kst = datetime.datetime.now(kst)
    cutoff_kst = now_kst - datetime.timedelta(hours=24)

    root = ET.fromstring(r.content)
    channel = root.find("channel")
    if channel is None:
        return []

    items: List[Dict[str, str]] = []
    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        if not title or not link:
            continue

        try:
            dt = parsedate_to_datetime(pub_date)
            if dt.tzinfo is None:
                dt = pytz.utc.localize(dt)
            dt_kst = dt.astimezone(kst)
        except Exception:
            continue

        if dt_kst < cutoff_kst:
            continue

        items.append(
            {
                "title": title,
                "url": link,
                "published": pub_date,
                "published_kst": dt_kst.isoformat(),
                "source": "Yahoo Finance",
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

    try:
        r = requests.get(url, headers=REQUEST_HEADERS, timeout=20)
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
    제목과 URL 기반 중복 병합.
    """
    out: List[Dict[str, str]] = []
    seen = set()
    for it in items:
        url_key = (it.get("url") or "").split("?")[0].rstrip("/")
        title_key = _norm_title(it.get("title", ""))
        key = url_key or title_key
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _clean_article_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"Advertisement\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Sign in to access.*", "", text, flags=re.IGNORECASE)
    return text.strip()


def fetch_article_text(url: str) -> str:
    """
    기사 URL에서 본문 문단을 추출한다. 접근이 막히거나 본문이 짧으면 빈 문자열을 반환한다.
    """
    if not url:
        return ""

    try:
        r = requests.get(url, headers=REQUEST_HEADERS, timeout=25, allow_redirects=True)
        r.raise_for_status()
    except Exception:
        return ""

    soup = BeautifulSoup(r.text, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "form", "nav", "footer", "header"]):
        tag.decompose()

    article = soup.find("article")
    containers = [article] if article else []
    containers.extend(
        soup.select(
            "[data-test-locator='articleBody'], [data-testid='article-body'], "
            ".caas-body, .article-body, .story-body, main"
        )
    )

    paragraphs: List[str] = []
    seen = set()
    for container in containers:
        if not container:
            continue
        for p in container.find_all(["p", "li"]):
            txt = _clean_article_text(p.get_text(" ", strip=True))
            if len(txt) < 40 or txt in seen:
                continue
            seen.add(txt)
            paragraphs.append(txt)

    if not paragraphs:
        for p in soup.find_all("p"):
            txt = _clean_article_text(p.get_text(" ", strip=True))
            if len(txt) < 60 or txt in seen:
                continue
            seen.add(txt)
            paragraphs.append(txt)

    text = "\n".join(paragraphs).strip()
    if len(text) < 250:
        return ""
    return text[:ARTICLE_CHAR_LIMIT]


def attach_article_texts(items: List[Dict[str, str]], max_articles: int = MAX_ARTICLES_PER_TICKER) -> List[Dict[str, str]]:
    """
    기사 목록의 각 URL을 열어 본문을 붙인다.
    """
    out: List[Dict[str, str]] = []
    for item in items[:max_articles]:
        text = fetch_article_text(item.get("url", ""))
        if not text:
            continue
        enriched = dict(item)
        enriched["text"] = text
        out.append(enriched)
        time.sleep(NEWS_SLEEP_SEC)
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
# Summarization: 10 lines (+ TSLA 20) from article contents
# -------------------------------
def summarize_ticker_lines_from_articles(
    ticker: str,
    company_name: str,
    articles: List[Dict[str, str]],
    n_lines: int,
) -> str:
    """
    Finviz와 Yahoo Finance에서 수집한 기사 본문으로 n_lines줄 이내 요약을 생성한다.
    """
    if not articles:
        return ""

    if not OPENAI_API_KEY:
        return "OPENAI_API_KEY 누락"

    article_blocks = []
    total_len = 0
    for i, it in enumerate(articles, start=1):
        title = (it.get("title") or "").strip()
        source = (it.get("source") or "").strip()
        published = (it.get("published_kst") or it.get("published") or "").strip()
        url = (it.get("url") or "").strip()
        text = (it.get("text") or "").strip()
        if not text:
            continue
        block = (
            f"[Article {i}]\n"
            f"Source: {source}\n"
            f"Published KST: {published}\n"
            f"Title: {title}\n"
            f"URL: {url}\n"
            f"Body:\n{text}"
        )
        if total_len + len(block) > LLM_INPUT_CHAR_LIMIT:
            break
        article_blocks.append(block)
        total_len += len(block)

    articles_text = "\n\n".join(article_blocks).strip()
    if not articles_text:
        return ""

    prompt = f"""
아래는 Finviz와 Yahoo Finance에서 수집한 최근 24시간 이내 기사 본문이다.

매우 중요:
- 요약은 오직 {company_name}({ticker})와 직접 관련된 내용만 포함해야 한다.
- 다른 기업, 시장 전체, 정치, 거시경제 관련 내용은 기사 본문에 포함되어 있어도 제외한다.
- {company_name}({ticker})의 실적, 제품, 전략, 주가, 규제, 사업과 직접 관련된 정보만 요약한다.

규칙:
- 아래 기사 본문에 있는 내용만 사용한다.
- {company_name}({ticker})와 직접 관련 없는 내용은 무시한다.
- 중복되는 사건은 하나로 병합한다.
- 최대 {n_lines}줄로 한국어 요약을 작성한다.
- 각 줄은 한 문장으로 작성한다.
- 번호, 불릿, 이모지, 마크다운, JSON은 사용하지 않는다.
- 추측, 평가, 전망, 일반론은 금지한다.
- 기사 본문에 없는 고유명사, 수치, 날짜, 원인은 새로 만들지 않는다.
- 내용이 적으면 적은 만큼만 출력하고, 부족하다는 말은 쓰지 않는다.

[기사 본문]
{articles_text}

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
        return "\n".join(lines)

    except Exception:
        return "요약 생성 실패"


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
    lines.append("[미국주식 데일리 브리핑 (Finviz + Yahoo Finance / 최근 24시간 / 기사 본문 기반)]")
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
    
        # 2) Finviz + Yahoo Finance 뉴스 수집
        raw: List[Dict[str, str]] = []
        errors: List[str] = []
        try:
            raw.extend(fetch_finviz_news_with_links_24h(t, max_items=80))
            time.sleep(FINVIZ_SLEEP_SEC)
        except Exception as e:
            errors.append(f"Finviz 수집 실패: {e}")

        try:
            raw.extend(fetch_yahoo_finance_news_24h(t, max_items=80))
            time.sleep(NEWS_SLEEP_SEC)
        except Exception as e:
            errors.append(f"Yahoo Finance 수집 실패: {e}")

        # 3) 중복 제거
        deduped_all = dedupe_news(raw)

        # 선택: 티커/회사명 필터를 추가하려면 여기에 적용한다.
        # deduped_all = filter_headlines_for_ticker(deduped_all, t, name)

        # 4) 실적 모드: 실적 헤드라인이 있으면 실적 관련만 남길 수 있다.
        #deduped, earnings_mode = filter_earnings_only_if_earnings_day(deduped_all)
        deduped = deduped_all
        earnings_mode = False

        # 5) 각 기사 URL에서 본문 수집
        articles = attach_article_texts(deduped, max_articles=MAX_ARTICLES_PER_TICKER)

        # 6) 기사 수가 적으면 5줄, 아니면 기본(TSLA 20 / others 10)
        n_lines = decide_summary_lines(t, n_headlines=len(articles))

        # 7) 요약
        summary = summarize_ticker_lines_from_articles(
            ticker=t,
            company_name=name,
            articles=articles,
            n_lines=n_lines,
        )
    
        # 8) 출력
        lines.append(f"{emoji} {t} - {name}{suffix}")
        if summary:
            lines.append(summary)
        if errors and not articles:
            lines.extend(errors)
        lines.append("\n---\n")

    return "\n".join(lines).strip()


# -------------------------------
# Main
# -------------------------------
def main() -> int:
    print("OpenAI key set?", bool(OPENAI_API_KEY))
    print("SMTP user set?", bool(SMTP_USER), "Email to:", EMAIL_TO)
    print("OpenAI model:", OPENAI_MODEL)

    kst = pytz.timezone("Asia/Seoul")
    today = datetime.datetime.now(kst).strftime("%Y-%m-%d")

    report_text = build_report_text(today)
    subject = f"미국주식 데일리 브리핑 {today}"
    ok = send_email_msg(report_text, subject)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
