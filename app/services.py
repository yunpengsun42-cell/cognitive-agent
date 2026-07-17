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
    build_scenario_analysis_prompt,
    build_entry_coach_prompt,
)


def _today() -> str:
    return date.today().isoformat()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _uid(user_id):
    return user_id if user_id is not None else db.USER_ID


def get_profile_text(user_id=None) -> str:
    uid = _uid(user_id)
    row = db.query_one(
        "SELECT * FROM diagnosis_profile WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (uid,),
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


def submit_diagnosis(answers, user_id=None):
    """answers: list of {question_id, question_text, answer_text}"""
    uid = _uid(user_id)
    db.execute("DELETE FROM diagnosis_answers WHERE user_id=?", (uid,))
    qa = []
    for a in answers:
        db.execute(
            "INSERT INTO diagnosis_answers (user_id, question_id, question_text, answer_text, created_at) "
            "VALUES (?,?,?,?,?)",
            (uid, a["question_id"], a["question_text"], a["answer_text"], _now()),
        )
        qa.append((a["question_text"], a["answer_text"]))
    raw = call_llm(SYSTEM_PROMPT, build_profile_prompt(qa), temperature=0.5)
    prof = _parse_profile(raw)
    db.execute("DELETE FROM diagnosis_profile WHERE user_id=?", (uid,))
    db.execute(
        "INSERT INTO diagnosis_profile "
        "(user_id, decision_style, emotion_trigger, recovery_speed, bias_tendency, summary_text, "
        "score_attention, score_memory, score_reasoning, score_executive, score_metacog, score_regulation, "
        "version, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?)",
        (
            uid,
            prof.get("decision_style"),
            prof.get("emotion_trigger"),
            prof.get("recovery_speed"),
            prof.get("bias_tendency"),
            prof.get("summary_text"),
            _to_score(prof.get("attention")),
            _to_score(prof.get("memory")),
            _to_score(prof.get("reasoning")),
            _to_score(prof.get("executive")),
            _to_score(prof.get("metacog")),
            _to_score(prof.get("regulation")),
            _now(),
        ),
    )
    return prof


def _to_score(v):
    """把 LLM 返回的评分安全地转成 1-100 整数,异常时 None。"""
    try:
        v = int(float(v))
        return max(1, min(100, v))
    except (TypeError, ValueError):
        return None


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


def get_profile_scores(user_id=None) -> dict:
    """读取最新诊断的六维分数,未诊断返回 None。"""
    uid = _uid(user_id)
    row = db.query_one(
        "SELECT score_attention, score_memory, score_reasoning, score_executive, "
        "score_metacog, score_regulation FROM diagnosis_profile "
        "WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (uid,),
    )
    if not row or row["score_attention"] is None:
        return None
    return {
        "attention": row["score_attention"],
        "memory": row["score_memory"],
        "reasoning": row["score_reasoning"],
        "executive": row["score_executive"],
        "metacog": row["score_metacog"],
        "regulation": row["score_regulation"],
    }


def create_entry(entry_type, raw_text, source_ref=None, user_id=None):
    uid = _uid(user_id)
    eid = db.execute(
        "INSERT INTO entries (user_id, entry_type, raw_text, source_ref, created_at) "
        "VALUES (?,?,?,?,?)",
        (uid, entry_type, raw_text, source_ref, _now()),
    )
    return eid


def generate_followup(entry_id):
    row = db.query_one("SELECT * FROM entries WHERE id=?", (entry_id,))
    if not row:
        return None
    raw = call_llm(
        SYSTEM_PROMPT,
        build_followup_prompt(row["raw_text"], get_profile_text(row["user_id"])),
        temperature=0.8,
    )
    db.execute("UPDATE entries SET ai_followup=? WHERE id=?", (raw, entry_id))
    return raw


def submit_reflection(entry_id, text):
    db.execute("UPDATE entries SET user_reflection=? WHERE id=?", (text, entry_id))


def submit_outcome(entry_id, text):
    db.execute("UPDATE entries SET outcome=? WHERE id=?", (text, entry_id))


def generate_entry_coaching(entry_id):
    """记一笔后自动生成 AI 教练点评 + 觉察度评分(0-100),入库到 entries。"""
    row = db.query_one("SELECT * FROM entries WHERE id=?", (entry_id,))
    if not row:
        return None
    raw = call_llm(
        SYSTEM_PROMPT,
        build_entry_coach_prompt(row["raw_text"], get_profile_text(row["user_id"])),
        temperature=0.7,
    )
    coach = _parse_entry_coach(raw)
    db.execute(
        "UPDATE entries SET ai_commentary=?, coach_score=?, ai_suggestion=? WHERE id=?",
        (coach.get("commentary"), coach.get("score"), coach.get("suggestion"), entry_id),
    )
    return coach


def _parse_entry_coach(raw):
    fallback = {"commentary": "", "score": None, "suggestion": ""}
    if not raw or "离线兜底" in raw:
        return fallback
    try:
        txt = raw.strip()
        if txt.startswith("```"):
            txt = txt.split("```", 2)[1]
            if txt.startswith("json"):
                txt = txt[4:]
        data = json.loads(txt)
        score = None
        try:
            score = max(1, min(100, int(float(data.get("score")))))
        except (TypeError, ValueError):
            score = None
        return {
            "commentary": (data.get("commentary") or "").strip(),
            "score": score,
            "suggestion": (data.get("suggestion") or "").strip(),
        }
    except Exception:
        return fallback


def generate_scenario_question(user_id=None):
    """生成今日场景题并写入 scenario_questions(若已存在则跳过)。"""
    uid = _uid(user_id)
    today = _today()
    exist = db.query_one(
        "SELECT id FROM scenario_questions WHERE user_id=? AND question_date=?",
        (uid, today),
    )
    if exist:
        return
    raw = call_llm(
        SYSTEM_PROMPT, build_scenario_prompt(get_profile_text(uid)), temperature=0.9
    )
    db.execute(
        "INSERT INTO scenario_questions (user_id, question_text, question_date, answered, created_at) "
        "VALUES (?,?,?,0,?)",
        (uid, raw, today, _now()),
    )


def get_today_scenario(user_id=None):
    uid = _uid(user_id)
    row = db.query_one(
        "SELECT * FROM scenario_questions WHERE user_id=? AND question_date=? ORDER BY id DESC LIMIT 1",
        (uid, _today()),
    )
    if row:
        return row
    generate_scenario_question(uid)
    return db.query_one(
        "SELECT * FROM scenario_questions WHERE user_id=? AND question_date=? ORDER BY id DESC LIMIT 1",
        (uid, _today()),
    )


def mark_scenario_answered(scenario_id):
    db.execute("UPDATE scenario_questions SET answered=1 WHERE id=?", (scenario_id,))


def get_scenario(scenario_id):
    row = db.query_one("SELECT * FROM scenario_questions WHERE id=?", (scenario_id,))
    return dict(row) if row else None


def generate_scenario_analysis(scenario_id):
    """根据场景题与用户作答,生成结构化复盘(思路点评/参考思路/改进建议)并入库。"""
    row = get_scenario(scenario_id)
    if not row or not row.get("answer_text"):
        return None
    raw = call_llm(
        SYSTEM_PROMPT,
        build_scenario_analysis_prompt(
            row["question_text"], row["answer_text"], get_profile_text(row["user_id"])
        ),
        temperature=0.7,
    )
    analysis = _parse_analysis(raw)
    db.execute(
        "UPDATE scenario_questions SET analysis_text=? WHERE id=?",
        (json.dumps(analysis, ensure_ascii=False), scenario_id),
    )
    return analysis


def _parse_analysis(raw):
    try:
        txt = raw.strip()
        if txt.startswith("```"):
            txt = txt.split("```", 2)[1]
            if txt.startswith("json"):
                txt = txt[4:]
        data = json.loads(txt)

        def _s(v):
            if isinstance(v, list):
                return "；".join(str(x).strip() for x in v if str(x).strip())
            return (v or "").strip()

        return {
            "commentary": _s(data.get("commentary")),
            "reference": _s(data.get("reference")),
            "suggestion": _s(data.get("suggestion")),
        }
    except Exception:
        return {"commentary": raw.strip(), "reference": "", "suggestion": ""}


def generate_fallback_reminder(user_id=None):
    uid = _uid(user_id)
    today = _today()
    has_entry = db.query_one(
        "SELECT id FROM entries WHERE user_id=? AND date(created_at)=?",
        (uid, today),
    )
    has_reminder = db.query_one(
        "SELECT id FROM reminders WHERE user_id=? AND remind_type='fallback' AND date(created_at)=?",
        (uid, today),
    )
    if has_entry or has_reminder:
        return
    db.execute(
        "INSERT INTO reminders (user_id, remind_type, title, content_text, is_read, created_at) "
        "VALUES (?, 'fallback', '今日训练提醒', '今天有没有哪个瞬间让你犹豫了一下?哪怕很小的事也可以记一笔。', 0, ?)",
        (uid, _now()),
    )


def get_unread_reminders(user_id=None):
    uid = _uid(user_id)
    return db.query(
        "SELECT * FROM reminders WHERE user_id=? AND is_read=0 ORDER BY id DESC",
        (uid,),
    )


def mark_reminder_read(rid):
    db.execute("UPDATE reminders SET is_read=1 WHERE id=?", (rid,))


def generate_weekly_summary(user_id=None):
    uid = _uid(user_id)
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    week_start, week_end = monday.isoformat(), sunday.isoformat()
    rows = db.query(
        "SELECT * FROM entries WHERE user_id=? AND date(created_at) BETWEEN ? AND ? ORDER BY id",
        (uid, week_start, week_end),
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
        (uid,),
    )
    streak = (prev["streak_weeks"] if prev else 0) + 1
    summary = call_llm(
        SYSTEM_PROMPT, build_weekly_prompt(entries_text, streak), temperature=0.6
    )
    db.execute(
        "INSERT INTO weekly_summaries (user_id, week_start, week_end, summary_text, streak_weeks, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (uid, week_start, week_end, summary, streak, _now()),
    )
    return summary


# ---------------- 真实训练引擎 ----------------

# 每个游戏主要训练的认知维度(用于把成绩映射到六维雷达)
TRAIN_GAME_DIMS = {
    "nback": ["memory", "attention"],
    "stroop": ["attention", "executive"],
    "gonogo": ["executive", "regulation"],
}

GAME_LABELS = {
    "nback": "N-back 工作记忆",
    "stroop": "Stroop 干扰抑制",
    "gonogo": "Go/No-Go 冲动控制",
}


def save_training_session(user_id, game, score, accuracy=None, rt_ms=None, level=None):
    uid = _uid(user_id)
    dims = ",".join(TRAIN_GAME_DIMS.get(game, []))
    db.execute(
        "INSERT INTO training_sessions "
        "(user_id, game, score, accuracy, rt_ms, level, dim_keys, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (uid, game, score, accuracy, rt_ms, level, dims, _now()),
    )


def recent_training(user_id=None, limit=20):
    uid = _uid(user_id)
    rows = db.query(
        "SELECT * FROM training_sessions WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (uid, limit),
    )
    return [dict(r) for r in rows]


def training_best(user_id=None):
    """返回每个游戏的历史最佳成绩 {game: best_score}。"""
    uid = _uid(user_id)
    rows = db.query(
        "SELECT game, MAX(score) AS best FROM training_sessions "
        "WHERE user_id=? GROUP BY game",
        (uid,),
    )
    return {r["game"]: r["best"] for r in rows}


def get_training_radar(user_id=None):
    """按维度聚合最近训练成绩,返回 6 维分数(无数据返回 None)。"""
    uid = _uid(user_id)
    rows = db.query(
        "SELECT score, dim_keys FROM training_sessions "
        "WHERE user_id=? ORDER BY id DESC LIMIT 60",
        (uid,),
    )
    if not rows:
        return None
    sums = {k: [0, 0] for k in
            ("attention", "memory", "reasoning", "executive", "metacog", "regulation")}
    for r in rows:
        try:
            sc = int(r["score"])
        except (TypeError, ValueError):
            continue
        keys = [k for k in (r["dim_keys"] or "").split(",") if k in sums]
        if not keys:
            continue
        for k in keys:
            sums[k][0] += sc
            sums[k][1] += 1
    out = {}
    for k, (s, c) in sums.items():
        out[k] = round(s / c) if c else 50
    return out
