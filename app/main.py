from pathlib import Path
import json
import uuid
import mimetypes
from datetime import datetime

from fastapi import FastAPI, Request, Form, Body, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from . import db, services, seed, auth, growth
from .scheduler import start as start_scheduler
from .prompts import DIAGNOSIS_QUESTIONS
from .classics_data import CATEGORY_ORDER

# 六大认知内核维度定义(顺序即雷达图顺序)
DIM_DEFS = [
    ("attention", "ATTENTION", "注意力",
     "聚焦目标、过滤干扰,在前额叶调控下维持稳定专注。", "舒尔特方格 · 单任务聚焦"),
    ("memory", "MEMORY", "记忆力",
     "编码、巩固与提取,工作记忆是你的实时心智工作台。", "间隔重复 · 工作记忆 N-back"),
    ("reasoning", "REASONING", "逻辑",
     "推演、归纳与判断,在不确定中形成可靠结论。", "每日一道推演题 · 写清论据"),
    ("executive", "EXECUTIVE", "执行功能",
     "计划、抑制与灵活切换,把意图落成有序行动。", "番茄钟 · 任务拆到可执行"),
    ("metacog", "METACOG", "元认知",
     "对思考的思考——觉察偏差、调控策略、校准判断。", "记一笔 + 追问 · 复盘偏差"),
    ("regulation", "REGULATION", "情绪调节",
     "在波动中稳住定力,让决策不被情绪劫持。", "呼吸锚定 · 情绪日志"),
]
DIM_ICONS = {
    "attention": '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3.2"/>',
    "memory": '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 9h8M8 13h8M8 17h5"/>',
    "reasoning": '<circle cx="6" cy="6" r="2.4"/><circle cx="18" cy="6" r="2.4"/><circle cx="12" cy="18" r="2.4"/><path d="M8 7l3 9M16 7l-3 9"/>',
    "executive": '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 12l3 3 5-6"/>',
    "metacog": '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>',
    "regulation": '<path d="M3 13c2.5 0 2.5-4 5-4s2.5 4 5 4 2.5-4 5-4 2.5 4 5 4"/><path d="M3 18c2.5 0 2.5-3 5-3s2.5 3 5 3 2.5-3 5-3"/>',
}

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))

app = FastAPI(title="认知内核训练智能体")
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")


@app.on_event("startup")
def on_startup():
    db.init_db()
    seed.seed_daoist_cards()
    seed.seed_classics()
    auth.ensure_owner()
    start_scheduler()


# ---------------- 账户 / 上下文 ----------------
def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_current_user(request: Request):
    return auth.get_user_by_token(request.cookies.get(auth.SESSION_COOKIE))


def ctx(request: Request) -> dict:
    user = get_current_user(request)
    return {
        "request": request,
        "user": user,
        "growth": growth.get_growth(user["id"]) if user else None,
        "is_guest": user is None,
        "is_admin": auth.is_admin(user),
    }


def uid_of(request: Request):
    u = get_current_user(request)
    return u["id"] if u else db.USER_ID


# ---------------- 页面路由 ----------------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    uid = uid_of(request)
    c = ctx(request)
    scenario = services.get_today_scenario(uid)
    if scenario:
        scenario = dict(scenario)
        if scenario.get("answered") and scenario.get("analysis_text"):
            try:
                scenario["analysis"] = json.loads(scenario["analysis_text"])
            except Exception:
                scenario["analysis"] = None
    news = db.query("SELECT * FROM daily_news ORDER BY id DESC LIMIT 5")
    training = db.query_one("SELECT * FROM training_news ORDER BY id DESC")
    reminders = services.get_unread_reminders(uid)
    streak_row = db.query_one(
        "SELECT streak_weeks FROM weekly_summaries WHERE user_id=? ORDER BY id DESC",
        (uid,),
    )
    has_profile = db.query_one(
        "SELECT id FROM diagnosis_profile WHERE user_id=? ORDER BY id DESC", (uid,)
    )
    rank = None
    if c["growth"]:
        board = growth.leaderboard(50)
        for i, b in enumerate(board, 1):
            if b["username"] == c["user"]["username"]:
                rank = i
                break
    classics_rows = db.query("SELECT * FROM classics ORDER BY id")
    recent_posts = db.query(
        "SELECT p.id, p.content, p.module, u.username FROM posts p "
        "JOIN users u ON u.id=p.user_id ORDER BY p.id DESC LIMIT 4"
    )
    board = growth.leaderboard(5)
    # A: 用真实诊断分数构造雷达与六维卡,未诊断用基线 50
    scores = services.get_profile_scores(uid)
    has_scores = scores is not None
    dimensions = []
    radar = []
    weakest_key = None
    if scores:
        weakest_key = min(scores, key=scores.get)
    for key, en, name, desc, reco in DIM_DEFS:
        w = scores[key] if scores else 50
        dimensions.append({
            "key": key, "en": en, "name": name, "desc": desc,
            "reco": reco, "score": w, "icon": DIM_ICONS[key],
        })
        radar.append({"k": name, "w": w})
    c.update({
        "scenario": scenario,
        "news": news,
        "training": training,
        "reminders": reminders,
        "streak": streak_row["streak_weeks"] if streak_row else 0,
        "has_profile": bool(has_profile),
        "questions": DIAGNOSIS_QUESTIONS,
        "classics_json": json.dumps([dict(r) for r in classics_rows], ensure_ascii=False),
        "rank": rank,
        "recent_posts": recent_posts,
        "board": board,
        "radar_scores": json.dumps(radar, ensure_ascii=False),
        "dimensions": dimensions,
        "weakest_key": weakest_key,
        "has_scores": has_scores,
    })
    return templates.TemplateResponse("home.html", c)


@app.post("/reminders/{rid}/read")
def reminder_read_page(rid: int):
    services.mark_reminder_read(rid)
    return RedirectResponse("/", status_code=303)


@app.post("/scenario/answer")
def scenario_answer(request: Request, raw_text: str = Form(...), scenario_id: int = Form(...)):
    uid = uid_of(request)
    services.create_entry("scenario_drill", raw_text, user_id=uid)
    services.mark_scenario_answered(scenario_id)
    db.execute("UPDATE scenario_questions SET answer_text=? WHERE id=?", (raw_text, scenario_id))
    services.generate_scenario_analysis(scenario_id)
    if uid != db.USER_ID:
        growth.award_xp(uid, growth.XP_ENTRY, "entry", {"scenario_id": scenario_id})
    return RedirectResponse("/", status_code=303)


@app.post("/practice/new")
def practice_new(request: Request, entry_type: str = Form("decision"),
                 raw_text: str = Form(...), source_ref: str = Form(None)):
    uid = uid_of(request)
    services.create_entry(entry_type, raw_text, source_ref, user_id=uid)
    if uid != db.USER_ID:
        growth.award_xp(uid, growth.XP_ENTRY, "entry", {"type": entry_type})
    return RedirectResponse("/practice", status_code=303)


@app.post("/practice/{eid}/followup")
def practice_followup(eid: int):
    services.generate_followup(eid)
    return RedirectResponse("/practice", status_code=303)


@app.post("/practice/{eid}/reflection")
def practice_reflection(eid: int, text: str = Form(...)):
    services.submit_reflection(eid, text)
    return RedirectResponse("/practice", status_code=303)


@app.post("/practice/{eid}/outcome")
def practice_outcome(eid: int, text: str = Form(...)):
    services.submit_outcome(eid, text)
    return RedirectResponse("/practice", status_code=303)


@app.get("/diagnosis", response_class=HTMLResponse)
def diagnosis_page(request: Request, done: int = 0):
    uid = uid_of(request)
    c = ctx(request)
    profile = db.query_one(
        "SELECT * FROM diagnosis_profile WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,)
    )
    c.update({"questions": DIAGNOSIS_QUESTIONS, "profile": profile, "done": done})
    return templates.TemplateResponse("diagnosis.html", c)


@app.post("/diagnosis/submit")
def diagnosis_submit(request: Request,
                     q0: str = Form(""), q1: str = Form(""), q2: str = Form(""),
                     q3: str = Form(""), q4: str = Form(""), q5: str = Form(""),
                     q6: str = Form(""), q7: str = Form(""), q8: str = Form(""),
                     q9: str = Form(""), q10: str = Form(""), q11: str = Form("")):
    uid = uid_of(request)
    answers = []
    vals = [q0, q1, q2, q3, q4, q5, q6, q7, q8, q9, q10, q11]
    for i, ans in enumerate(vals):
        if ans.strip():
            answers.append({
                "question_id": f"q{i+1}",
                "question_text": DIAGNOSIS_QUESTIONS[i],
                "answer_text": ans.strip(),
            })
    services.submit_diagnosis(answers, uid)
    if uid != db.USER_ID:
        growth.award_xp(uid, growth.XP_DIAGNOSIS, "diagnosis")
    return RedirectResponse("/diagnosis?done=1", status_code=303)


@app.get("/practice", response_class=HTMLResponse)
def practice_page(request: Request):
    uid = uid_of(request)
    c = ctx(request)
    entries = db.query(
        "SELECT * FROM entries WHERE user_id=? ORDER BY id DESC LIMIT 30", (uid,)
    )
    c.update({"entries": entries})
    return templates.TemplateResponse("practice.html", c)


@app.get("/report", response_class=HTMLResponse)
def report_page(request: Request):
    uid = uid_of(request)
    c = ctx(request)
    latest = db.query_one(
        "SELECT * FROM weekly_summaries WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,)
    )
    history = db.query(
        "SELECT * FROM weekly_summaries WHERE user_id=? ORDER BY id DESC LIMIT 10 OFFSET 1", (uid,)
    )
    c.update({"latest": latest, "history": history})
    return templates.TemplateResponse("report.html", c)


@app.post("/report/generate")
def report_generate(request: Request):
    uid = uid_of(request)
    services.generate_weekly_summary(uid)
    return RedirectResponse("/report", status_code=303)


@app.get("/classics", response_class=HTMLResponse)
def classics_page(request: Request, category: str = ""):
    uid = uid_of(request)
    rows = db.query("SELECT * FROM classics ORDER BY id")
    cards = [dict(r) for r in rows]
    cats = [c for c in CATEGORY_ORDER
            if any(card["category"] == c for card in cards)]
    fav_ids = []
    if uid != db.USER_ID:
        fav_ids = [r["classic_id"] for r in
                   db.query("SELECT classic_id FROM classics_fav WHERE user_id=?", (uid,))]
    c = ctx(request)
    c.update({
        "categories": cats,
        "classics_json": json.dumps(cards, ensure_ascii=False),
        "fav_ids": fav_ids,
        "active_category": category,
    })
    return templates.TemplateResponse("classics.html", c)


@app.get("/community", response_class=HTMLResponse)
def community_page(request: Request):
    uid = uid_of(request)
    c = ctx(request)
    posts = db.query(
        "SELECT p.*, u.username FROM posts p JOIN users u ON u.id=p.user_id "
        "ORDER BY p.id DESC LIMIT 40"
    )
    liked = set()
    if uid != db.USER_ID:
        liked = {r["post_id"] for r in db.query(
            "SELECT post_id FROM post_likes WHERE user_id=?", (uid,))}
    post_list = []
    for p in posts:
        pc = dict(p)
        pc["like_count"] = db.query_one(
            "SELECT COUNT(*) AS c FROM post_likes WHERE post_id=?", (p["id"],))["c"]
        pc["comment_count"] = db.query_one(
            "SELECT COUNT(*) AS c FROM post_comments WHERE post_id=?", (p["id"],))["c"]
        pc["liked"] = p["id"] in liked
        pc["comments"] = [dict(r) for r in db.query(
            "SELECT c.*, u.username FROM post_comments c "
            "JOIN users u ON u.id=c.user_id WHERE c.post_id=? ORDER BY c.id", (p["id"],))]
        post_list.append(pc)
    c.update({"posts": post_list, "leaderboard": growth.leaderboard(10)})
    return templates.TemplateResponse("community.html", c)


# ---------------- 账户路由 ----------------

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    if get_current_user(request):
        return RedirectResponse("/", status_code=303)
    c = ctx(request)
    c.update({"mode": "login", "next": next})
    return templates.TemplateResponse("auth.html", c)


@app.post("/login")
def login_post(request: Request, username: str = Form(...), password: str = Form(...),
               next: str = Form("/")):
    user = auth.authenticate(username.strip(), password)
    if not user:
        c = ctx(request)
        c.update({"mode": "login", "next": next, "error": "用户名或密码错误"})
        return templates.TemplateResponse("auth.html", c)
    token = auth.create_session(user["id"])
    resp = RedirectResponse(next or "/", status_code=303)
    resp.set_cookie(auth.SESSION_COOKIE, token, httponly=True, samesite="lax",
                    max_age=auth.SESSION_DAYS * 24 * 3600)
    return resp


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    if get_current_user(request):
        return RedirectResponse("/", status_code=303)
    c = ctx(request)
    c.update({"mode": "register"})
    return templates.TemplateResponse("auth.html", c)


@app.post("/register")
def register_post(request: Request, username: str = Form(...),
                  password: str = Form(...), email: str = Form("")):
    uid, err = auth.register(username.strip(), password, email.strip() or None)
    if err:
        c = ctx(request)
        c.update({"mode": "register", "error": err})
        return templates.TemplateResponse("auth.html", c)
    token = auth.create_session(uid)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(auth.SESSION_COOKIE, token, httponly=True, samesite="lax",
                    max_age=auth.SESSION_DAYS * 24 * 3600)
    return resp


@app.get("/logout")
def logout(request: Request):
    auth.logout(request.cookies.get(auth.SESSION_COOKIE))
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(auth.SESSION_COOKIE)
    return resp


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    u = get_current_user(request)
    if not auth.is_admin(u):
        return RedirectResponse("/login", status_code=303)
    c = ctx(request)
    users = db.query(
        "SELECT u.id, u.username, u.email, u.is_admin, u.created_at, "
        "g.xp, g.level, g.streak_days "
        "FROM users u LEFT JOIN user_growth g ON g.user_id=u.id "
        "ORDER BY u.id"
    )
    c.update({
        "users": users,
        "total_users": db.query_one("SELECT COUNT(*) AS c FROM users")["c"],
        "total_entries": db.query_one("SELECT COUNT(*) AS c FROM entries")["c"],
        "total_posts": db.query_one("SELECT COUNT(*) AS c FROM posts")["c"],
        "total_classics_fav": db.query_one("SELECT COUNT(*) AS c FROM classics_fav")["c"],
        "total_diagnosis": db.query_one("SELECT COUNT(*) AS c FROM diagnosis_profile")["c"],
    })
    return templates.TemplateResponse("admin.html", c)


# ---------------- 文件上传 ----------------

@app.post("/api/upload/image")
def api_upload_image(request: Request, file: UploadFile = File(...)):
    u = get_current_user(request)
    if not u:
        return JSONResponse({"error": "请先登录"}, status_code=401)
    if not file.content_type or not file.content_type.startswith("image/"):
        return JSONResponse({"error": "仅支持图片文件"}, status_code=400)
    data = file.file.read()
    if len(data) > 5 * 1024 * 1024:
        return JSONResponse({"error": "图片不能超过 5MB"}, status_code=400)
    ext = mimetypes.guess_extension(file.content_type) or ""
    if ext in (".jpe", ".jpeg"):
        ext = ".jpg"
    # 安全白名单:拒绝 SVG 等可被执行脚本的格式,防止存储型 XSS
    if ext not in (".jpg", ".png", ".gif", ".webp"):
        return JSONResponse(
            {"error": "不支持的图片格式,仅支持 jpg / png / gif / webp"},
            status_code=400,
        )
    filename = f"{uuid.uuid4().hex[:16]}_{datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"
    dest = UPLOAD_DIR / filename
    dest.write_bytes(data)
    return {"ok": True, "url": f"/uploads/{filename}"}


# ---------------- 社交 API ----------------

@app.post("/api/post")
def api_post(request: Request, content: str = Body(...), module: str = Body("打卡"), image_url: str = Body("")):
    u = get_current_user(request)
    if not u:
        return JSONResponse({"error": "请先登录"}, status_code=401)
    content = (content or "").strip()
    if not content:
        return JSONResponse({"error": "内容不能为空"}, status_code=400)
    image_url = (image_url or "").strip()
    # 仅允许站内上传的图片,避免外链追踪/钓鱼与潜在 XSS
    if image_url and not image_url.startswith("/uploads/"):
        image_url = ""
    pid = db.execute(
        "INSERT INTO posts (user_id, content, module, image_url, xp_gain, created_at) VALUES (?,?,?,?,?,?)",
        (u["id"], content, module, image_url or None, growth.XP_POST, _now()),
    )
    g = growth.award_xp(u["id"], growth.XP_POST, "post")
    return {"ok": True, "id": pid, "growth": g}


@app.post("/api/post/like")
def api_post_like(request: Request, post_id: int = Body(..., embed=True)):
    u = get_current_user(request)
    if not u:
        return JSONResponse({"error": "请先登录"}, status_code=401)
    existing = db.query_one("SELECT 1 FROM post_likes WHERE post_id=? AND user_id=?",
                            (post_id, u["id"]))
    if existing:
        db.execute("DELETE FROM post_likes WHERE post_id=? AND user_id=?", (post_id, u["id"]))
        liked = False
    else:
        db.execute("INSERT INTO post_likes (post_id, user_id, created_at) VALUES (?,?,?)",
                   (post_id, u["id"], _now()))
        liked = True
    cnt = db.query_one("SELECT COUNT(*) AS c FROM post_likes WHERE post_id=?", (post_id,))["c"]
    return {"ok": True, "liked": liked, "count": cnt}


@app.post("/api/post/comment")
def api_post_comment(request: Request, post_id: int = Body(...), content: str = Body(...)):
    u = get_current_user(request)
    if not u:
        return JSONResponse({"error": "请先登录"}, status_code=401)
    content = (content or "").strip()
    if not content:
        return JSONResponse({"error": "评论不能为空"}, status_code=400)
    db.execute("INSERT INTO post_comments (post_id, user_id, content, created_at) VALUES (?,?,?,?)",
               (post_id, u["id"], content, _now()))
    cnt = db.query_one("SELECT COUNT(*) AS c FROM post_comments WHERE post_id=?", (post_id,))["c"]
    return {"ok": True, "count": cnt}


# ---------------- 国学收藏 API ----------------

@app.get("/api/classics/favs")
def api_classics_favs(request: Request):
    u = get_current_user(request)
    if not u:
        return []
    return [r["classic_id"] for r in
            db.query("SELECT classic_id FROM classics_fav WHERE user_id=?", (u["id"],))]


@app.post("/api/classics/fav")
def api_classics_fav(request: Request, classic_id: int = Body(..., embed=True)):
    u = get_current_user(request)
    if not u:
        return JSONResponse({"error": "请先登录"}, status_code=401)
    existing = db.query_one("SELECT 1 FROM classics_fav WHERE user_id=? AND classic_id=?",
                            (u["id"], classic_id))
    if existing:
        db.execute("DELETE FROM classics_fav WHERE user_id=? AND classic_id=?",
                   (u["id"], classic_id))
        fav = False
    else:
        db.execute("INSERT INTO classics_fav (user_id, classic_id, created_at) VALUES (?,?,?)",
                   (u["id"], classic_id, _now()))
        fav = True
        growth.award_xp(u["id"], growth.XP_CLASSIC, "classic", {"id": classic_id})
    return {"ok": True, "fav": fav}


# ---------------- JSON API(供程序化调用 / 验收) ----------------

@app.get("/api/diagnosis/questions")
def api_diag_questions():
    return [{"question_id": f"q{i+1}", "question_text": q}
            for i, q in enumerate(DIAGNOSIS_QUESTIONS)]


@app.post("/api/diagnosis/complete")
def api_diag_complete(answers: list = Body(...)):
    prof = services.submit_diagnosis(answers)
    return prof


@app.post("/api/entry")
def api_entry(entry_type: str = Body(...), raw_text: str = Body(...),
              source_ref: str = Body(None)):
    eid = services.create_entry(entry_type, raw_text, source_ref)
    return {"id": eid}


@app.get("/api/entry/today-question")
def api_today_question(request: Request):
    s = services.get_today_scenario(uid_of(request))
    return dict(s) if s else {}


@app.post("/api/entry/{eid}/followup")
def api_followup(eid: int):
    f = services.generate_followup(eid)
    return {"followup": f}


@app.post("/api/entry/{eid}/reflection")
def api_reflection(eid: int, text: str = Body(...)):
    services.submit_reflection(eid, text)
    return {"ok": True}


@app.post("/api/entry/{eid}/outcome")
def api_outcome(eid: int, text: str = Body(...)):
    services.submit_outcome(eid, text)
    return {"ok": True}


@app.get("/api/news/daily")
def api_news_daily():
    return [dict(r) for r in db.query("SELECT * FROM daily_news ORDER BY id DESC LIMIT 5")]


@app.get("/api/news/training")
def api_news_training():
    r = db.query_one("SELECT * FROM training_news ORDER BY id DESC")
    return dict(r) if r else {}


@app.get("/api/report/weekly/latest")
def api_report_latest(request: Request):
    r = db.query_one(
        "SELECT * FROM weekly_summaries WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (uid_of(request),),
    )
    return dict(r) if r else {}


@app.post("/api/report/weekly/generate")
def api_report_generate(request: Request):
    s = services.generate_weekly_summary(uid_of(request))
    return {"ok": bool(s), "summary": s}


@app.get("/api/daoist/match")
def api_daoist_match(scenario: str = ""):
    if not scenario:
        return {"error": "scenario required"}
    r = db.query_one(
        "SELECT * FROM daoist_cards WHERE applicable_scenario LIKE ? LIMIT 1",
        (f"%{scenario}%",),
    )
    return dict(r) if r else {}


@app.get("/api/classics")
def api_classics(category: str = ""):
    if category:
        rows = db.query("SELECT * FROM classics WHERE category=? ORDER BY id", (category,))
    else:
        rows = db.query("SELECT * FROM classics ORDER BY id")
    return [dict(r) for r in rows]


@app.get("/api/reminders")
def api_reminders(request: Request):
    return [dict(r) for r in services.get_unread_reminders(uid_of(request))]


@app.post("/api/reminders/{rid}/read")
def api_reminder_read(rid: int):
    services.mark_reminder_read(rid)
    return {"ok": True}
