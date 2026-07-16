from pathlib import Path
import json
from fastapi import FastAPI, Request, Form, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from . import db, services, seed
from .scheduler import start as start_scheduler
from .prompts import DIAGNOSIS_QUESTIONS
from .classics_data import CATEGORY_ORDER

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))

app = FastAPI(title="认知内核训练智能体")


@app.on_event("startup")
def on_startup():
    db.init_db()
    seed.seed_daoist_cards()
    seed.seed_classics()
    start_scheduler()


# ---------------- 页面路由 ----------------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    scenario = services.get_today_scenario()
    news = db.query("SELECT * FROM daily_news ORDER BY id DESC LIMIT 5")
    training = db.query_one("SELECT * FROM training_news ORDER BY id DESC")
    reminders = services.get_unread_reminders()
    streak_row = db.query_one(
        "SELECT streak_weeks FROM weekly_summaries ORDER BY id DESC"
    )
    has_profile = db.query_one(
        "SELECT id FROM diagnosis_profile WHERE user_id=? ORDER BY id DESC",
        (db.USER_ID,),
    )
    classics_rows = db.query("SELECT * FROM classics ORDER BY id")
    classics_json = json.dumps([dict(r) for r in classics_rows], ensure_ascii=False)
    return templates.TemplateResponse("home.html", {
        "request": request,
        "scenario": scenario,
        "news": news,
        "training": training,
        "reminders": reminders,
        "streak": streak_row["streak_weeks"] if streak_row else 0,
        "has_profile": bool(has_profile),
        "questions": DIAGNOSIS_QUESTIONS,
        "classics_json": classics_json,
    })


@app.post("/reminders/{rid}/read")
def reminder_read_page(rid: int):
    services.mark_reminder_read(rid)
    return RedirectResponse("/", status_code=303)


@app.post("/scenario/answer")
def scenario_answer(raw_text: str = Form(...), scenario_id: int = Form(...)):
    services.create_entry("scenario_drill", raw_text)
    services.mark_scenario_answered(scenario_id)
    return RedirectResponse("/", status_code=303)


@app.post("/practice/new")
def practice_new(entry_type: str = Form("decision"),
                 raw_text: str = Form(...),
                 source_ref: str = Form(None)):
    services.create_entry(entry_type, raw_text, source_ref)
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
    profile = db.query_one(
        "SELECT * FROM diagnosis_profile WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (db.USER_ID,),
    )
    return templates.TemplateResponse("diagnosis.html", {
        "request": request,
        "questions": DIAGNOSIS_QUESTIONS,
        "profile": profile,
        "done": done,
    })


@app.post("/diagnosis/submit")
def diagnosis_submit(request: Request,
                     q0: str = Form(""), q1: str = Form(""), q2: str = Form(""),
                     q3: str = Form(""), q4: str = Form(""), q5: str = Form(""),
                     q6: str = Form(""), q7: str = Form(""), q8: str = Form(""),
                     q9: str = Form(""), q10: str = Form(""), q11: str = Form("")):
    answers = []
    vals = [q0, q1, q2, q3, q4, q5, q6, q7, q8, q9, q10, q11]
    for i, ans in enumerate(vals):
        if ans.strip():
            answers.append({
                "question_id": f"q{i+1}",
                "question_text": DIAGNOSIS_QUESTIONS[i],
                "answer_text": ans.strip(),
            })
    services.submit_diagnosis(answers)
    return RedirectResponse("/diagnosis?done=1", status_code=303)


@app.get("/practice", response_class=HTMLResponse)
def practice_page(request: Request):
    entries = db.query(
        "SELECT * FROM entries WHERE user_id=? ORDER BY id DESC LIMIT 30",
        (db.USER_ID,),
    )
    return templates.TemplateResponse("practice.html", {
        "request": request,
        "entries": entries,
    })


@app.get("/report", response_class=HTMLResponse)
def report_page(request: Request):
    latest = db.query_one(
        "SELECT * FROM weekly_summaries ORDER BY id DESC LIMIT 1"
    )
    history = db.query(
        "SELECT * FROM weekly_summaries ORDER BY id DESC LIMIT 10 OFFSET 1"
    )
    return templates.TemplateResponse("report.html", {
        "request": request,
        "latest": latest,
        "history": history,
    })


@app.post("/report/generate")
def report_generate():
    services.generate_weekly_summary()
    return RedirectResponse("/report", status_code=303)


@app.get("/classics", response_class=HTMLResponse)
def classics_page(request: Request, category: str = ""):
    rows = db.query("SELECT * FROM classics ORDER BY id")
    cards = [dict(r) for r in rows]
    cats = [c for c in CATEGORY_ORDER
            if any(card["category"] == c for card in cards)]
    return templates.TemplateResponse("classics.html", {
        "request": request,
        "categories": cats,
        "classics_json": json.dumps(cards, ensure_ascii=False),
        "active_category": category,
    })


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
def api_today_question():
    s = services.get_today_scenario()
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
def api_report_latest():
    r = db.query_one("SELECT * FROM weekly_summaries ORDER BY id DESC LIMIT 1")
    return dict(r) if r else {}


@app.post("/api/report/weekly/generate")
def api_report_generate():
    s = services.generate_weekly_summary()
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
def api_reminders():
    return [dict(r) for r in services.get_unread_reminders()]


@app.post("/api/reminders/{rid}/read")
def api_reminder_read(rid: int):
    services.mark_reminder_read(rid)
    return {"ok": True}
