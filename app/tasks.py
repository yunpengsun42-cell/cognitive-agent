import os
import re
from datetime import datetime, date

import feedparser

from . import db
from . import services
from .llm import call_llm
from .prompts import SYSTEM_PROMPT, build_training_question_prompt

RSS_CAT_HINTS = {
    "36kr": "科技",
    "wallstreetcn": "经济",
    "zaobao": "时政",
    "hnrss": "科技",
    "caixin": "经济",
}


def _guess_cat(url: str) -> str:
    for k, v in RSS_CAT_HINTS.items():
        if k in url:
            return v
    return "社会"


def _clean(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)  # 去掉 RSS 的 HTML 标签
    return re.sub(r"\s+", " ", text).strip()


def fetch_daily_news():
    """抓取 RSS → daily_news(3-5条) + training_news(1条,含AI追问)。每日只跑一次。"""
    today = date.today().isoformat()
    existing = db.query_one(
        "SELECT COUNT(*) AS c FROM daily_news WHERE published_date=?", (today,)
    )
    feeds = [f.strip() for f in os.getenv("RSS_FEEDS", "").split(",") if f.strip()]
    items = []
    for url in feeds:
        try:
            d = feedparser.parse(url)
            for e in d.entries[:5]:
                items.append({
                    "title": (e.get("title") or "").strip(),
                    "summary": _clean((e.get("summary") or e.get("description") or "")[:400]),
                    "link": e.get("link", ""),
                    "category": _guess_cat(url),
                })
        except Exception as ex:
            print(f"[news] 抓取失败 {url}: {ex}")
    if not items:
        return

    if not (existing and existing["c"] >= 5):
        seen = set()
        count = 0
        for it in items:
            if not it["title"] or it["title"] in seen:
                continue
            seen.add(it["title"])
            db.execute(
                "INSERT INTO daily_news (title, summary, source_url, category, published_date, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (it["title"], it["summary"], it["link"], it["category"], today,
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
            count += 1
            if count >= 5:
                break

    # 训练精选(每天1条 + AI追问)
    has_training = db.query_one(
        "SELECT id FROM training_news WHERE published_date=?", (today,)
    )
    if not has_training:
        pick = items[0]
        q = call_llm(
            SYSTEM_PROMPT,
            build_training_question_prompt(pick["summary"] or pick["title"]),
            temperature=0.9,
        )
        db.execute(
            "INSERT INTO training_news (title, summary, source_url, ai_question, published_date, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (pick["title"], pick["summary"], pick["link"], q, today,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )


def generate_scenario_job():
    services.generate_scenario_question()


def fallback_job():
    services.generate_fallback_reminder()


def weekly_job():
    services.generate_weekly_summary()
