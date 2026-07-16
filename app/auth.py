"""身份与会话:注册 / 登录 / 退出,密码 pbkdf2 加盐哈希,httpOnly 会话 cookie。

零第三方依赖(全部标准库),可直接跑在 CloudStudio / 本地。
"""
import hashlib
import secrets
from datetime import datetime, timedelta

from . import db

SESSION_COOKIE = "sid"
SESSION_DAYS = 30

# 站长(owner)账号. 建议首次登录后修改用户名,但这里提供一个可工作的初始入口.
# 在 CloudStudio 部署前最好把 OWNER_PASSWORD 改为你自己记得住的强密码.
OWNER_USERNAME = "owner"
OWNER_PASSWORD = "cognitive2026!"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------- 密码 ----------
def hash_password(pw: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), 100_000)
    return f"pbkdf2_sha256$100000${salt}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    if not stored or "$" not in stored:
        return False
    try:
        _algo, iters, salt, want = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), int(iters))
        return secrets.compare_digest(dk.hex(), want)
    except Exception:
        return False


# ---------- 站长(owner) ----------
def ensure_owner():
    """启动时保证存在一个 owner 账号,可看到所有用户数据."""
    row = db.query_one("SELECT id, password_hash FROM users WHERE username=?", (OWNER_USERNAME,))
    if row:
        if not row["password_hash"]:
            db.execute(
                "UPDATE users SET password_hash=?, is_admin=1 WHERE id=?",
                (hash_password(OWNER_PASSWORD), row["id"]),
            )
        else:
            db.execute(
                "UPDATE users SET is_admin=1 WHERE id=?",
                (row["id"],),
            )
        return
    uid = db.execute(
        "INSERT INTO users (username, password_hash, created_at) VALUES (?,?,?)",
        (OWNER_USERNAME, hash_password(OWNER_PASSWORD), _now()),
    )
    db.execute(
        "INSERT OR IGNORE INTO user_growth "
        "(user_id, xp, level, streak_days, badges, last_active_date, updated_at) "
        "VALUES (?,0,1,0,'[]',NULL,?)",
        (uid, _now()),
    )


def is_admin(user: dict) -> bool:
    return bool(user and user.get("is_admin"))


# ---------- 注册 / 认证 ----------
def register(username: str, password: str, email: str = None):
    if not username or not password:
        return None, "用户名和密码必填"
    if len(password) < 6:
        return None, "密码至少 6 位"
    if db.query_one("SELECT id FROM users WHERE username=?", (username,)):
        return None, "用户名已被占用"
    uid = db.execute(
        "INSERT INTO users (username, email, password_hash, created_at) "
        "VALUES (?,?,?,?)",
        (username, email, hash_password(password), _now()),
    )
    db.execute(
        "INSERT OR IGNORE INTO user_growth "
        "(user_id, xp, level, streak_days, badges, last_active_date, updated_at) "
        "VALUES (?,0,1,0,'[]',NULL,?)",
        (uid, _now()),
    )
    return uid, None


def authenticate(username: str, password: str):
    row = db.query_one("SELECT * FROM users WHERE username=?", (username,))
    if not row or not verify_password(password, row["password_hash"] or ""):
        return None
    return dict(row)


# ---------- 会话 ----------
def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
        (token, user_id, _now(), expires),
    )
    return token


def get_user_by_token(token: str):
    if not token:
        return None
    s = db.query_one("SELECT * FROM sessions WHERE token=?", (token,))
    if not s:
        return None
    if datetime.strptime(s["expires_at"], "%Y-%m-%d %H:%M:%S") < datetime.now():
        db.execute("DELETE FROM sessions WHERE token=?", (token,))
        return None
    u = db.query_one("SELECT * FROM users WHERE id=?", (s["user_id"],))
    return dict(u) if u else None


def logout(token: str):
    if token:
        db.execute("DELETE FROM sessions WHERE token=?", (token,))
