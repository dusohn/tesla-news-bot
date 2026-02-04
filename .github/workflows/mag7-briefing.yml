import requests
import os
import datetime
import pytz
import sys
import json
import time
from typing import Dict, List, Any, Optional

from urllib.parse import quote
from bs4 import BeautifulSoup

# Pillow (PNG 생성)
from PIL import Image, ImageDraw, ImageFont

# -------------------------------
# Environment Variables
# -------------------------------
OPENAI_API_KEY = (os.environ.get("OPENAI_API_KEY") or "").strip()
TELEGRAM_TOKEN = (os.environ.get("TELEGRAM_TOKEN") or "").strip()
CHAT_ID = (os.environ.get("CHAT_ID") or "").strip()

OPENAI_MODEL = (os.environ.get("OPENAI_MODEL") or "gpt-4o-mini").strip()
OPENAI_URL = "https://api.openai.com/v1/responses"

# ✅ 필요에 맞게 주석 해제/조정
MAG7 = [
    {"name": "Apple", "ticker": "AAPL", "emoji": "🍎"},
    {"name": "Microsoft", "ticker": "MSFT", "emoji": "💻"},
    {"name": "Amazon", "ticker": "AMZN", "emoji": "📦"},
    {"name": "Alphabet", "ticker": "GOOGL", "emoji": "🔍"},
    {"name": "Meta", "ticker": "META", "emoji": "🧠"},
    {"name": "NVIDIA", "ticker": "NVDA", "emoji": "🤖"},
    {"name": "Tesla", "ticker": "TSLA", "emoji": "🚗"},
]

THEMES = ["AI", "로봇", "광고", "클라우드", "반도체", "전기차", "로보택시", "실적", "규제", "거시"]
MAX_PER_TICKER = 5
MAX_LINES = 5
KW_PER_THEME = 3
MAX_THEMES_PER_TICKER = 5

# Telegram 메시지 길이 제한(4096) 대응
TELEGRAM_CHUNK_SIZE = 3900

# Finviz 요청 간 딜레이(봇 차단 완화)
FINVIZ_SLEEP_SEC = 1.0


# -------------------------------
# 1) Fetch Finviz headlines (last 24h only)
# -------------------------------
def fetch_finviz_news(ticker: str, max_items: int = 40) -> List[Dict[str, str]]:
    """
    Finviz quote 페이지의 뉴스 테이블에서 뉴스 수집.
    """
    url = f"https://finviz.com/quote.ashx?t={quote(ticker)}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }

    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "lxml")
    table = soup.find("table", class_="news-table")
    if not table:
        return []

    items: List[Dict[str, str]] = []
    rows = table.find_all("tr")
    for row in rows:
        tds = row.find_all("td")
        if len(tds) < 2:
            continue

        dt_txt = tds[0].get_text(" ", strip=True)  # "Today, 6:40 AM" or "Feb-03, 6:40 AM"
        a = tds[1].find("a")
        title = a.get_text(" ", strip=True) if a else tds[1].get_text(" ", strip=True)

        if title:
            items.append({"title": title, "published": dt_txt})

        if len(items) >= max_items:
            break

    return items


def _parse_finviz_datetime_to_kst(dt_txt: str, now_kst: datetime.datetime) -> Optional[datetime.datetime]:
    """
    Finviz 표기 (Today, 6:40 AM) / (Feb-03, 6:40 AM) 등을
    US/Eastern 기준으로 해석 후 KST datetime으로 변환.
    """
    if not dt_txt:
        return None

    et = pytz.timezone("US/Eastern")
    kst = pytz.timezone("Asia/Seoul")

    now_et = now_kst.astimezone(et)
    year = now_et.year

    # Case 1: "Today, 6:40 AM"
    if dt_txt.lower().startswith("today"):
        time_part = dt_txt.split(",", 1)[-1].strip()
        try:
            t = datetime.datetime.strptime(time_part, "%I:%M %p").time()
        except Exception:
            return None
        dt_et = et.localize(datetime.datetime(year, now_et.month, now_et.day, t.hour, t.minute))
        return dt_et.astimezone(kst)

    # Case 2: "Feb-03, 6:40 AM" (또는 변형)
    norm = dt_txt.replace("-", " ").replace(",", "")
    parts = norm.split()
    if len(parts) >= 4:
        try:
            mon = parts[0]
            day = int(parts[1])
            time_str = " ".join(parts[2:4])  # "6:40 AM"
            t = datetime.datetime.strptime(time_str, "%I:%M %p").time()
            month_num = datetime.datetime.strptime(mon, "%b").month

            dt_et = et.localize(datetime.datetime(year, month_num, day, t.hour, t.minute))

            # 연말/연초 경계 보정: 미래로 튀면 작년으로
            if dt_et > now_et + datetime.timedelta(hours=1):
                dt_et = et.localize(datetime.datetime(year - 1, month_num, day, t.hour, t.minute))

            return dt_et.astimezone(kst)
        except Exception:
            return None

    return None


def filter_last_24h(items: List[Dict[str, str]], now_kst: datetime.datetime) -> List[Dict[str, str]]:
    cutoff = now_kst - datetime.timedelta(hours=24)
    out = []
    for it in items:
        pub = (it.get("published") or "").strip()
        dt_kst = _parse_finviz_datetime_to_kst(pub, now_kst)
        if dt_kst and dt_kst >= cutoff:
            out.append(it)
    return out


def get_mag7_news(per_ticker: int = MAX_PER_TICKER) -> Dict[str, Any]:
    print("Fetching news (Finviz, last 24h)...")
    kst = pytz.timezone("Asia/Seoul")
    now_kst = datetime.datetime.now(kst)

    items: Dict[str, List[Dict[str, str]]] = {}

    for c in MAG7:
        ticker = c["ticker"]
        try:
            raw = fetch_finviz_news(ticker, max_items=60)
            recent = filter_last_24h(raw, now_kst=now_kst)
            items[ticker] = recent[:per_ticker]
            print(f"- {ticker}: {len(items[ticker])} headlines (last 24h)")
        except Exception as e:
            print(f"⚠️ Finviz fetch failed for {ticker}: {e}")
            items[ticker] = []

        time.sleep(FINVIZ_SLEEP_SEC)

    total = sum(len(v) for v in items.values())
    print(f"Total headlines (last 24h): {total}")
    return {"source": "Finviz (quote page news)", "items": items}


# -------------------------------
# 2) OpenAI JSON summarization (with themes)
# -------------------------------
def _extract_output_text(res_json: dict) -> str:
    text_parts = []
    for item in (res_json.get("output") or []):
        for c in (item.get("content") or []):
            if c.get("type") == "output_text" and isinstance(c.get("text"), str):
                text_parts.append(c["text"])
    return "\n".join(t.strip() for t in text_parts if t and t.strip()).strip()


def _dynamic_schema_block(tickers: List[str], today: str) -> str:
    """
    MAG7 리스트가 일부만 켜져 있어도 스키마가 안 깨지게,
    by_ticker를 '현재 tickers'로만 요구하도록 스키마 텍스트 생성.
    """
    # 예시 티커 하나로 템플릿 만들고, 실제 요구는 tickers 전체로
    exemplar = tickers[0] if tickers else "AAPL"
    schema_lines = [
        "{",
        f'  "date_kst": "{today}",',
        '  "universe": "Magnificent 7",',
        '  "overall": {',
        '    "key_takeaways": ["문장","문장","문장","문장","문장"],',
        '    "market_mood": {',
        '      "label": "긍정|중립|부정",',
        '      "reason": "한 줄 이유"',
        "    }",
        "  },",
        '  "by_ticker": {',
    ]

    # tickers 각각을 명시적으로 요구(모델이 빠뜨리는 것 방지)
    for i, t in enumerate(tickers):
        comma = "," if i < len(tickers) - 1 else ""
        schema_lines += [
            f'    "{t}": {{',
            '      "themes": [',
            '        {"theme":"AI","keywords":["키워드","키워드","키워드"]}',
            "      ],",
            f'      "headline_translations": ["한글 번역"(최대 {MAX_PER_TICKER}개)],',
            '      "summary": {',
            f'        "bullish": ["호재"(최대 {MAX_LINES}개)],',
            f'        "bearish": ["악재"(최대 {MAX_LINES}개)],',
            f'        "watchlist": ["관전 포인트"(최대 {MAX_LINES}개)]',
            "      },",
            '      "mood": "긍정|중립|부정"',
            f"    }}{comma}",
        ]

    schema_lines += [
        "  }",
        "}",
    ]
    return "\n".join(schema_lines)


def summarize_mag7_to_json(news_blob: Dict[str, Any], today: str) -> Optional[Dict[str, Any]]:
    print("Analyzing with ChatGPT (OpenAI Responses API) - JSON output...")

    if not OPENAI_API_KEY:
        print("❌ OPENAI_API_KEY missing")
        return None

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_API_KEY}"}

    tickers = [c["ticker"] for c in MAG7]

    compact_lines = []
    for c in MAG7:
        t = c["ticker"]
        name = c["name"]
        headlines = news_blob["items"].get(t, [])
        for i, h in enumerate(headlines, start=1):
            title = h.get("title", "")
            published = h.get("published", "")
            if published:
                compact_lines.append(f"{t} ({name}) H{i}: {title} [{published}]")
            else:
                compact_lines.append(f"{t} ({name}) H{i}: {title}")
    headlines_text = "\n".join(compact_lines).strip()

    theme_list = ", ".join(THEMES)
    schema_block = _dynamic_schema_block(tickers=tickers, today=today)

    prompt = f"""
너는 미국 주식 시장 뉴스 애널리스트야.
아래는 오늘(최근 24시간 이내) 수집된 헤드라인이다.

반드시 '유효한 JSON만' 출력해. (마크다운/코드블록/설명 문장 금지)

테마는 반드시 아래 목록 중에서만 선택해:
[{theme_list}]

스키마(반드시 준수):
{schema_block}

규칙:
- overall.key_takeaways는 정확히 5개.
- themes는 티커당 1~{MAX_THEMES_PER_TICKER}개(가능하면 2~4개).
- 각 theme.keywords는 정확히 3개(짧게, 명사형, 중복 피하기).
- headline_translations 최대 {MAX_PER_TICKER}개.
- bullish/bearish/watchlist 각 최대 {MAX_LINES}개(없으면 빈 배열 가능).
- 전부 한국어(영어/URL 금지), 한 줄 문장으로 짧게.

[헤드라인 데이터]
{headlines_text}
""".strip()

    # Responses API: 가능하면 json_object 강제(모델/계정에 따라 미지원일 수 있어 fallback 포함)
    body = {
        "model": OPENAI_MODEL,
        "input": prompt,
        "response_format": {"type": "json_object"},
    }

    try:
        r = requests.post(OPENAI_URL, headers=headers, json=body, timeout=75)
        if r.status_code != 200:
            # json_object 미지원 등일 수 있어 fallback으로 재시도
            print(f"⚠️ OpenAI API non-200 (try fallback) {r.status_code}: {r.text[:300]}")
            body.pop("response_format", None)
            r = requests.post(OPENAI_URL, headers=headers, json=body, timeout=75)
    except requests.RequestException as e:
        print(f"❌ OpenAI request failed: {e}")
        return None

    if r.status_code != 200:
        print(f"❌ OpenAI API error {r.status_code}: {r.text[:800]}")
        return None

    try:
        j = r.json()
    except Exception:
        print("❌ OpenAI response not JSON (outer)")
        return None

    out_text = _extract_output_text(j).strip()
    if not out_text:
        return None

    try:
        return json.loads(out_text)
    except json.JSONDecodeError:
        # tolerate stray text
        start = out_text.find("{")
        end = out_text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(out_text[start:end + 1])
            except Exception:
                pass
        print("❌ Failed to parse JSON from model output.")
        return None


# -------------------------------
# 3) Render: JSON -> Card-style text (Telegram/PNG 공용)
# -------------------------------
def safe_list(x) -> List[Any]:
    return x if isinstance(x, list) else []


def safe_dict(x) -> Dict[str, Any]:
    return x if isinstance(x, dict) else {}


def render_mag7_cards(summary: Dict[str, Any], news_blob: Dict[str, Any]) -> str:
    date_kst = (summary.get("date_kst") or "").strip()
    overall = safe_dict(summary.get("overall"))
    key_takeaways = safe_list(overall.get("key_takeaways"))
    market_mood = safe_dict(overall.get("market_mood"))
    overall_label = (market_mood.get("label") or "").strip()
    overall_reason = (market_mood.get("reason") or "").strip()

    lines: List[str] = []
    lines.append("🧠 [미국주식 데일리 브리핑 (Finviz / 최근 24시간)]")
    if date_kst:
        lines.append(f"📅 {date_kst}")
    lines.append("")

    lines.append("📌 전체 핵심 요약")
    for t in key_takeaways[:5]:
        if isinstance(t, str) and t.strip():
            lines.append(f"• {t.strip()}")
    if overall_label:
        lines.append(f"📊 전체 시장 분위기: {overall_label}" + (f" — {overall_reason}" if overall_reason else ""))
    lines.append("\n---\n")

    by_ticker = safe_dict(summary.get("by_ticker"))

    for c in MAG7:
        t = c["ticker"]
        name = c["name"]
        emoji = c["emoji"]

        data = safe_dict(by_ticker.get(t))
        mood = (data.get("mood") or "중립").strip()

        themes = safe_list(data.get("themes"))
        translations = safe_list(data.get("headline_translations"))
        summary_obj = safe_dict(data.get("summary"))
        bullish = safe_list(summary_obj.get("bullish"))
        bearish = safe_list(summary_obj.get("bearish"))
        watchlist = safe_list(summary_obj.get("watchlist"))

        # fallback: 모델 번역이 없으면 원문 헤드라인 대신 "요약용 한글 제목"이 없어서 영문이 나올 수 있음
        # 여기서는 어쩔 수 없이 원문 제목을 노출하되 라벨을 "헤드라인"으로 처리
        fallback_is_english = False
        if not translations:
            orig = news_blob.get("items", {}).get(t, [])
            translations = [h.get("title", "").strip() for h in orig if h.get("title", "").strip()]
            fallback_is_english = True

        lines.append(f"{emoji} {t} — {name}")
        lines.append(f"시장 분위기: {mood}")

        # theme tags
        if themes:
            themed_bits = []
            for th in themes[:MAX_THEMES_PER_TICKER]:
                thd = safe_dict(th)
                theme_name = (thd.get("theme") or "").strip()
                kws = [k.strip() for k in safe_list(thd.get("keywords"))[:KW_PER_THEME] if isinstance(k, str) and k.strip()]
                if theme_name and kws:
                    themed_bits.append(f"{theme_name}({', '.join(kws)})")
                elif theme_name:
                    themed_bits.append(theme_name)
            if themed_bits:
                lines.append("🏷️ 테마: " + " | ".join(themed_bits))

        lines.append("")
        if bullish:
            lines.append("✅ 호재")
            for x in bullish[:MAX_LINES]:
                if isinstance(x, str) and x.strip():
                    lines.append(f"• {x.strip()}")
            lines.append("")
        if bearish:
            lines.append("⚠️ 악재")
            for x in bearish[:MAX_LINES]:
                if isinstance(x, str) and x.strip():
                    lines.append(f"• {x.strip()}")
            lines.append("")
        if watchlist:
            lines.append("👀 관전 포인트")
            for x in watchlist[:MAX_LINES]:
                if isinstance(x, str) and x.strip():
                    lines.append(f"• {x.strip()}")
            lines.append("")

        if translations:
            headline_label = "📰 주요 헤드라인(번역)" if not fallback_is_english else "📰 주요 헤드라인"
            lines.append(headline_label)
            for h in translations[:MAX_PER_TICKER]:
                if isinstance(h, str) and h.strip():
                    lines.append(f"• {h.strip()}")

        lines.append("\n---\n")

    return "\n".join(lines).strip()


# -------------------------------
# 4) Save PNG
# -------------------------------
def _load_font(size: int) -> ImageFont.ImageFont:
    """
    GitHub Actions(Ubuntu) 포함, 한글 표시 가능한 폰트를 우선 로드.
    """
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKkr-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/AppleGothic.ttf",
        "C:\\Windows\\Fonts\\malgun.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
    ]

    for p in candidates:
        try:
            if os.path.exists(p):
                return ImageFont.truetype(p, size=size)
        except Exception:
            pass

    return ImageFont.load_default()


def save_report_png(text: str, date_str: str) -> str:
    """
    card 텍스트를 PNG로 저장.
    GitHub Actions 환경에서는 ~/Downloads가 없을 수 있어 cwd로 저장됨.
    """
    downloads = os.path.join(os.path.expanduser("~"), "Downloads")
    if not os.path.isdir(downloads):
        downloads = os.getcwd()

    out_path = os.path.join(downloads, f"{date_str}.png")

    W = 1080
    margin = 60
    line_spacing = 10

    font_title = _load_font(42)
    font_body = _load_font(30)

    dummy = Image.new("RGB", (W, 100), "white")
    d = ImageDraw.Draw(dummy)

    def wrap_line(line: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
        if not line.strip():
            return [""]
        chars = list(line)
        out = []
        cur = ""
        for ch in chars:
            test = cur + ch
            try:
                ok = d.textlength(test, font=font) <= max_width
            except Exception:
                # 일부 환경에서 이모지/폰트 문제 시 대략적인 fallback
                ok = len(test) * (font.size * 0.6) <= max_width
            if ok:
                cur = test
            else:
                out.append(cur)
                cur = ch
        out.append(cur)
        return out

    max_text_width = W - 2 * margin
    lines_raw = text.splitlines()

    wrapped: List[tuple] = []
    for i, ln in enumerate(lines_raw):
        if i == 0:
            for wln in wrap_line(ln, font_title, max_text_width):
                wrapped.append(("title", wln))
        else:
            for wln in wrap_line(ln, font_body, max_text_width):
                wrapped.append(("body", wln))

    y = margin
    for kind, ln in wrapped:
        font = font_title if kind == "title" else font_body
        bbox = d.textbbox((0, 0), ln, font=font)
        h = (bbox[3] - bbox[1]) if bbox else (50 if kind == "title" else 36)
        y += h + line_spacing
    H = y + margin

    img = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    y = margin
    for kind, ln in wrapped:
        font = font_title if kind == "title" else font_body
        draw.text((margin, y), ln, font=font, fill=(0, 0, 0))
        bbox = draw.textbbox((margin, y), ln, font=font)
        h = (bbox[3] - bbox[1]) if bbox else (50 if kind == "title" else 36)
        y += h + line_spacing

    img.save(out_path, "PNG")
    return out_path


# -------------------------------
# 5) Telegram (chunked)
# -------------------------------
def send_telegram_msg(message: str) -> bool:
    print("Sending Telegram...")
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("❌ Telegram env vars missing")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    chunks = [message[i:i + TELEGRAM_CHUNK_SIZE] for i in range(0, len(message), TELEGRAM_CHUNK_SIZE)]
    for idx, chunk in enumerate(chunks, start=1):
        payload = {"chat_id": CHAT_ID, "text": chunk, "disable_web_page_preview": True}
        resp = requests.post(url, data=payload, timeout=20)
        if resp.status_code != 200:
            print(f"❌ Telegram send failed (part {idx}/{len(chunks)}): {resp.text}")
            return False

    print("✅ Telegram sent")
    return True


# -------------------------------
# 6) Main
# -------------------------------
def main():
    print("OpenAI key set?", bool(OPENAI_API_KEY))
    print("Token set?", bool(TELEGRAM_TOKEN), "ChatID set?", bool(CHAT_ID))
    print("OpenAI model:", OPENAI_MODEL)

    kst = pytz.timezone("Asia/Seoul")
    today = datetime.datetime.now(kst).strftime("%Y-%m-%d")

    news_blob = get_mag7_news(per_ticker=MAX_PER_TICKER)
    summary_json = summarize_mag7_to_json(news_blob, today=today)

    if summary_json is None:
        # fallback summary
        summary_json = {
            "date_kst": today,
            "universe": "Magnificent 7",
            "overall": {
                "key_takeaways": [],
                "market_mood": {"label": "중립", "reason": "요약 생성 실패"}
            },
            "by_ticker": {
                t["ticker"]: {
                    "themes": [],
                    "headline_translations": [],
                    "summary": {"bullish": [], "bearish": [], "watchlist": []},
                    "mood": "중립"
                } for t in MAG7
            }
        }

    # 1) 카드 텍스트 생성
    report_text = render_mag7_cards(summary_json, news_blob)

    # 2) Telegram 전송
    ok_tg = send_telegram_msg(report_text)

    # 3) PNG 저장
    try:
        out_path = save_report_png(report_text, today)
        print(f"✅ Saved PNG: {out_path}")
    except Exception as e:
        print(f"❌ PNG save failed: {e}")
        out_path = ""

    return 0 if ok_tg else 1


if __name__ == "__main__":
    sys.exit(main())
