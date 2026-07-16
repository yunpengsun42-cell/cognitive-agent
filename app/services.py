import json
from datetime import datetime, date, timedelta

from . import db
from .llm import call_llm
from .prompts import (
    SYSTEM_PROMPT,
    build_profile_prompt,
    build_scenario_prompt,
    build_followup_prompt,
    build_weekly_prompt,
    build_training_question_prompt,
)


def _today() -> str:
    return date.today().isoformat()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_profile_text(user_id=db.USER_ID) -> str:
    row = db.query_one(
        "SELECT * FROM diagnosis_profile WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    if not row:
        return "（用户尚未完成诊断,暂无画像）"
    return (
        f"决策风格:{row['decision_style'] or '未知'}\n"
        f"情绪敏感源:{row['emotion_trigger'] or '未知'}\n"
        f"抗压恢复速度:{row['recovery_speed'] or '未知'}\n"
        f"认知偏差倾向:{row['bias_tendency'] or '未知'}\n"
        f"画像概述:{row['summary_text'] or ''}"
    )


def submit_diagnosis(answers):
    """answers: list of {question_id, question_text, answer_text}"""
    user_id = db.USER_ID
    # 清掉旧答案,避免重复
    db.execute("DELETE FROM diagnosis_answers WHERE user_id=?", (user_id,))
    qa = []
    for a in answers:
        db.execute(
            "INSERT INTO diagnosis_answers (user_id, question_id, question_text, answer_text, created_at) "
            "VALUES (?,?,?,?,?)",
            (user_id, a["question_id"], a["question_text"], a["answer_text"], _now()),
        )
        qa.append((a["question_text"], a["answer_text"]))
    raw = call_llm(SYSTEM_PROMPT, build_profile_prompt(qa), temperature=0.5)
    prof = _parse_profile(raw)
    db.execute(
        "DELETE FROM diagnosis_profile WHERE user_id=?", (user_id,)
    )
    db.execute(
        "INSERT INTO diagnosis_profile "
        "(user_id, decision_style, emotion_trigger, recovery_speed, bias_tendency, summary_text, version, created_at) "
        "VALUES (?,?,?,?,?,?,1,?)",
        (
            user_id,
            prof.get("decision_style"),
            prof.get("emotion_trigger"),
            prof.get("recovery_speed"),
            prof.get("bias_tendency"),
            prof.get("summary_text"),
            _now(),
        ),
    )
    return prof


def _parse_profile(raw):
    try:
        txt = raw.strip()
        if txt.startswith("```"):
            txt = txt.split("```", 2)[1]
            if txt.startswith("json"):
                txt = txt[4:]
        return json.loads(txt)
    except Exception:
        return {"summary_text": raw, "decision_style": "", "emotion_trigger": "",
                "recovery_speed": "", "bias_tendency": ""}


def create_entry(entry_type, raw_text, source_ref=None):
    eid = db.execute(
        "INSERT INTO entries (user_id, entry_type, raw_text, source_ref, created_at) "
        "VALUES (?,?,?,?,?)",
        (db.USER_ID, entry_type, raw_text, source_ref, _now()),
    )
    return eid


def generate_followup(entry_id):
    row = db.query_one("SELECT * FROM entries WHERE id=?", (entry_id,))
    if not row:
        return None
    raw = call_llm(
        SYSTEM_PROMPT,
        build_followup_prompt(row["raw_text"], get_profile_text()),
        temperature=0.8,
    )
    db.execute(
        "UPDATE entries SET ai_followup=? WHERE id=?", (raw, entry_id)
    )
    return raw


def submit_reflection(entry_id, text):
    db.execute(
        "UPDATE entries SET user_reflection=? WHERE id=?", (text, entry_id)
    )


def submit_outcome(entry_id, text):
    db.execute("UPDATE entries SET outcome=? WHERE id=?", (text, entry_id))


def generate_scenario_question():
    """生成今日场景题并写入 scenario_questions(若已存在则跳过)。"""
    today = _today()
    exist = db.query_one(
        "SELECT id FROM scenario_questions WHERE user_id=? AND question_date=?",
        (db.USER_ID, today),
    )
    if exist:
        return
    raw = call_llm(
        SYSTEM_PROMPT, build_scenario_prompt(get_profile_text()), temperature=0.9
    )
    db.execute(
        "INSERT INTO scenario_questions (user_id, question_text, question_date, answered, created_at) "
        "VALUES (?,?,?,0,?)",
        (db.USER_ID, raw, today, _now()),
    )


def get_today_scenario():
    row = db.query_one(
        "SELECT * FROM scenario_questions WHERE user_id=? AND question_date=? ORDER BY id DESC LIMIT 1",
        (db.USER_ID, _today()),
    )
    if row:
        return row
    # 按需即时生成(与定时任务一致)
    generate_scenario_question()
    return db.query_one(
        "SELECT * FROM scenario_questions WHERE user_id=? AND question_date=? ORDER BY id DESC LIMIT 1",
        (db.USER_ID, _today()),
    )


def mark_scenario_answered(scenario_id):
    db.execute("UPDATE scenario_questions SET answered=1 WHERE id=?", (scenario_id,))


def generate_fallback_reminder():
    today = _today()
    has_entry = db.query_one(
        "SELECT id FROM entries WHERE user_id=? AND date(created_at)=?",
        (db.USER_ID, today),
    )
    has_reminder = db.query_one(
        "SELECT id FROM reminders WHERE user_id=? AND remind_type='fallback' AND date(created_at)=?",
        (db.USER_ID, today),
    )
    if has_entry or has_reminder:
        return
    db.execute(
        "INSERT INTO reminders (user_id, remind_type, title, content_text, is_read, created_at) "
        "VALUES (?, 'fallback', '今日训练提醒', '今天有没有哪个瞬间让你犹豫了一下?哪怕很小的事也可以记一笔。', 0, ?)",
        (db.USER_ID, _now()),
    )


def get_unread_reminders():
    return db.query(
        "SELECT * FROM reminders WHERE user_id=? AND is_read=0 ORDER BY id DESC",
        (db.USER_ID,),
    )


def mark_reminder_read(rid):
    db.execute("UPDATE reminders SET is_read=1 WHERE id=?", (rid,))


def generate_weekly_summary():
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    week_start, week_end = monday.isoformat(), sunday.isoformat()
    rows = db.query(
        "SELECT * FROM entries WHERE user_id=? AND date(created_at) BETWEEN ? AND ? ORDER BY id",
        (db.USER_ID, week_start, week_end),
    )
    if not rows:
        return None
    entries_text = "\n".join(
        f"[{r['entry_type']}] {r['raw_text']}"
        + (f"\n  追问:{r['ai_followup']}" if r["ai_followup"] else "")
        + (f"\n  回应:{r['user_reflection']}" if r["user_reflection"] else "")
        for r in rows
    )
    prev = db.query_one(
        "SELECT streak_weeks FROM weekly_summaries WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (db.USER_ID,),
    )
    streak = (prev["streak_weeks"] if prev else 0) + 1
    summary = call_llm(
        SYSTEM_PROMPT, build_weekly_prompt(entries_text, streak), temperature=0.6
    )
    db.execute(
        "INSERT INTO weekly_summaries (user_id, week_start, week_end, summary_text, streak_weeks, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (db.USER_ID, week_start, week_end, summary, streak, _now()),
    )
    return summary
