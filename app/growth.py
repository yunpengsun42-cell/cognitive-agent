"""成长体系:经验值 / 等级 / 连续打卡 / 成就徽章 / 排行榜。

等级曲线:从 Lv.n 升到 Lv.n+1 需要 n*50 经验(越往后越慢,但有正反馈)。
访客(guest, user_id == db.USER_ID)不参与成长。
"""
import json
from datetime import date, datetime, timedelta

from . import db

XP_DIAGNOSIS = 50
XP_ENTRY = 10
XP_CLASSIC = 5
XP_POST = 15
XP_DAILY = 5


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def level_info(xp: int):
    """返回 (等级, 当前级已攒经验, 当前级所需经验)。"""
    level = 1
    remaining = xp
    while True:
        need = 50 * level
        if remaining >= need:
            remaining -= need
            level += 1
        else:
            return level, remaining, need


def get_growth(user_id):
    if not user_id or user_id == db.USER_ID:
        return None
    g = db.query_one("SELECT * FROM user_growth WHERE user_id=?", (user_id,))
    if not g:
        return None
    level, progress, need = level_info(g["xp"])
    badges = json.loads(g["badges"] or "[]")
    return {
        "xp": g["xp"],
        "level": level,
        "progress": progress,
        "need": need,
        "streak": g["streak_days"],
        "badges": badges,
    }


def award_xp(user_id, amount: int, action: str, meta=None):
    """给用户加经验;顺带处理每日连续打卡与徽章。返回成长 dict(访客返回 None)。"""
    if not user_id or user_id == db.USER_ID:
        return None
    db.execute(
        "INSERT OR IGNORE INTO user_growth "
        "(user_id, xp, level, streak_days, badges, last_active_date, updated_at) "
        "VALUES (?,0,1,0,'[]',NULL,?)",
        (user_id, _now()),
    )
    g = db.query_one("SELECT * FROM user_growth WHERE user_id=?", (user_id,))
    today = date.today().isoformat()
    xp = g["xp"] + amount
    streak = g["streak_days"]
    if g["last_active_date"] != today:
        # 新的一天:给每日登录/活跃奖励 + 更新连续天数
        xp += XP_DAILY
        if g["last_active_date"] == (date.today() - timedelta(days=1)).isoformat():
            streak += 1
        else:
            streak = 1
    badges = _award_badges(
        json.loads(g["badges"] or "[]"), user_id, action, xp, streak
    )
    db.execute(
        "UPDATE user_growth SET xp=?, streak_days=?, last_active_date=?, badges=?, updated_at=? "
        "WHERE user_id=?",
        (xp, streak, today, json.dumps(badges, ensure_ascii=False), _now(), user_id),
    )
    db.execute(
        "INSERT INTO usage_log (user_id, action, meta, created_at) VALUES (?,?,?,?)",
        (user_id, action, json.dumps(meta, ensure_ascii=False) if meta else None, _now()),
    )
    return get_growth(user_id)


def _award_badges(badges, user_id, action, xp, streak):
    def add(code, name):
        if not any(b["code"] == code for b in badges):
            badges.append({"code": code, "name": name})

    if action == "diagnosis":
        add("diag", "初诊 · 照见自我")
    if action == "post":
        add("post", "登台 · 首次发声")
    if streak >= 7:
        add("streak7", "七日筑基")
    if xp >= 500:
        add("xp500", "厚积薄发")
    fav_count = db.query_one(
        "SELECT COUNT(*) AS c FROM classics_fav WHERE user_id=?", (user_id,)
    )["c"]
    if action == "classic" and fav_count >= 10:
        add("book10", "书海拾遗")
    return badges


def leaderboard(limit: int = 10):
    rows = db.query(
        "SELECT u.username, g.xp, g.level, g.streak_days "
        "FROM user_growth g JOIN users u ON u.id=g.user_id "
        "WHERE u.username IS NOT NULL ORDER BY g.xp DESC LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in rows]
