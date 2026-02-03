import feedparser
import requests
import os
import datetime
import pytz
import sys
import json
import textwrap
from typing import Dict, List, Any, Optional

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


# -------------------------------
# 1) Fetch RSS headlines
# -------------------------------
def fetch_google_news_rss(query: str, max_items: int = 5) -> List[Dict[str, str]]:
    rss_url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    feed = feedparser.parse(rss_url)

    items = []
    for entry in (feed.entries or [])[:max_items]:
        title = getattr(entry, "title", "").strip()
        published = getattr(entry, "published", "").strip()
        if title:
            items.append({"title": title, "published": published})
    return items


def get_mag7_news(per_ticker: int = MAX_PER_TICKER) -> Dict[str, Any]:
    print("Fetching news (Magnificent 7)...")
    items: Dict[str, List[Dict[str, str]]] = {}

    for c in MAG7:
        ticker = c["ticker"]
        q = f"{ticker}%20stock%20news"
        headlines = fetch_google_news_rss(q, max_items=per_ticker)
        items[ticker] = headlines
        print(f"- {ticker}: {len(headlines)} headlines")

    total = sum(len(v) for v in items.values())
    print(f"Total headlines: {total}")
    return {"source": "Google News RSS", "items": items}


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


def summarize_mag7_to_json(news_blob: Dict[str, Any], today: str) -> Optional[Dict[str, Any]]:
    print("Analyzing with ChatGPT (OpenAI Responses API) - JSON output...")

    if not OPENAI_API_KEY:
        print("❌ OPENAI_API_KEY missing")
        return None

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_API_KEY}"}

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

    prompt = f"""
너는 미국 주식 시장 뉴스 애널리스트야.
아래는 오늘 수집된 Magnificent 7(AAPL, MSFT, AMZN, GOOGL, META, NVDA, TSLA) 헤드라인이다.

반드시 '유효한 JSON만' 출력해. (마크다운/코드블록/설명 문장 금지)

테마는 반드시 아래 목록 중에서만 선택해:
[{theme_list}]

스키마(반드시 준수):
{{
  "date_kst": "{today}",
  "universe": "Magnificent 7",
  "overall": {{
    "key_takeaways": ["문장","문장","문장","문장","문장"],
    "market_mood": {{
      "label": "긍정|중립|부정",
      "reason": "한 줄 이유"
    }}
  }},
  "by_ticker": {{
    "AAPL": {{
      "themes": [
        {{"theme":"AI","keywords":["키워드","키워드","키워드"]}}
      ],
      "headline_translations": ["한글 번역"(최대 {MAX_PER_TICKER}개)],
      "summary": {{
        "bullish": ["호재"(최대 {MAX_LINES}개)],
        "bearish": ["악재"(최대 {MAX_LINES}개)],
        "watchlist": ["관전 포인트"(최대 {MAX_LINES}개)]
      }},
      "mood": "긍정|중립|부정"
    }},
    "MSFT": {{ "...동일..." }},
    "AMZN": {{ "...동일..." }},
    "GOOGL": {{ "...동일..." }},
    "META": {{ "...동일..." }},
    "NVDA": {{ "...동일..." }},
    "TSLA": {{ "...동일..." }}
  }}
}}

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

    body = {"model": OPENAI_MODEL, "input": prompt}

    try:
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
    lines.append("🧠 [미국주식 Magnificent 7 데일리 브리핑]")
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

        if not translations:
            orig = news_blob.get("items", {}).get(t, [])
            translations = [h.get("title", "").strip() for h in orig if h.get("title", "").strip()]

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
            for x in bullish[:5]:
                if isinstance(x, str) and x.strip():
                    lines.append(f"• {x.strip()}")
            lines.append("")
        if bearish:
            lines.append("⚠️ 악재")
            for x in bearish[:5]:
                if isinstance(x, str) and x.strip():
                    lines.append(f"• {x.strip()}")
            lines.append("")
        if watchlist:
            lines.append("👀 관전 포인트")
            for x in watchlist[:5]:
                if isinstance(x, str) and x.strip():
                    lines.append(f"• {x.strip()}")
            lines.append("")

        if translations:
            lines.append("📰 주요 헤드라인(번역)")
            for h in translations[:5]:
                if isinstance(h, str) and h.strip():
                    lines.append(f"• {h.strip()}")

        lines.append("\n---\n")

    return "\n".join(lines).strip()


# -------------------------------
# 4) Save PNG to Downloads
# -------------------------------
def _load_font(size: int) -> ImageFont.ImageFont:
    """
    가능한 경우 시스템 폰트를 사용. 실패하면 기본 폰트.
    """
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/Library/Fonts/AppleGothic.ttf",  # macOS
        "C:\\Windows\\Fonts\\malgun.ttf",  # Windows
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
    기본 저장 위치: ~/Downloads/YYYY-MM-DD.png
    """
    # 저장 경로
    #downloads = os.path.join(os.path.expanduser("~"), "Downloads")
    downloads = "C:/Users/dusoh/Downloads/"
    #if not os.path.isdir(downloads):
    #    # GitHub Actions 같은 환경에서 Downloads가 없을 수 있음 -> 현재 폴더로
    #    downloads = os.getcwd()

    #out_path = os.path.join(downloads, f"{date_str}.png")
    out_path = downloads + f"{date_str}.png"

    # 이미지 스타일
    W = 1080  # 인스타/모바일 보기 좋은 폭
    margin = 60
    line_spacing = 10

    font_title = _load_font(42)
    font_body = _load_font(30)

    # 텍스트 wrapping: 폭에 맞춰 줄바꿈
    # (폰트 폭은 draw.textlength로 측정)
    dummy = Image.new("RGB", (W, 100), "white")
    d = ImageDraw.Draw(dummy)

    def wrap_line(line: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
        if not line.strip():
            return [""]
        words = list(line)
        # 한글/이모지 대비: "글자 단위"로 폭을 맞추는 방식(안전)
        out = []
        cur = ""
        for ch in words:
            test = cur + ch
            if d.textlength(test, font=font) <= max_width:
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
        # 첫 줄은 제목 느낌으로 크게
        if i == 0:
            for wln in wrap_line(ln, font_title, max_text_width):
                wrapped.append(("title", wln))
        else:
            for wln in wrap_line(ln, font_body, max_text_width):
                wrapped.append(("body", wln))

    # 높이 계산
    y = margin
    for kind, ln in wrapped:
        font = font_title if kind == "title" else font_body
        bbox = d.textbbox((0, 0), ln, font=font)
        h = (bbox[3] - bbox[1]) if bbox else (50 if kind == "title" else 36)
        y += h + line_spacing
    H = y + margin

    # 이미지 생성
    img = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    y = margin
    for kind, ln in wrapped:
        font = font_title if kind == "title" else font_body
        fill = (0, 0, 0)
        draw.text((margin, y), ln, font=font, fill=fill)
        bbox = draw.textbbox((margin, y), ln, font=font)
        h = (bbox[3] - bbox[1]) if bbox else (50 if kind == "title" else 36)
        y += h + line_spacing

    img.save(out_path, "PNG")
    return out_path


# -------------------------------
# 5) Telegram
# -------------------------------
def send_telegram_msg(message: str) -> bool:
    print("Sending Telegram...")
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("❌ Telegram env vars missing")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "disable_web_page_preview": True}

    resp = requests.post(url, data=payload, timeout=20)
    if resp.status_code != 200:
        print(f"❌ Telegram send failed: {resp.text}")
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

    # 3) PNG 저장 (다운로드 폴더 / 파일명 = 날짜.png)
    try:
        out_path = save_report_png(report_text, today)
        print(f"✅ Saved PNG: {out_path}")
    except Exception as e:
        print(f"❌ PNG save failed: {e}")
        out_path = ""

    return 0 if ok_tg else 1


if __name__ == "__main__":
    sys.exit(main())
