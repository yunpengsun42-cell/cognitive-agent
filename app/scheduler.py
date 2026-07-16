from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import tasks

scheduler = BackgroundScheduler(timezone="Asia/Shanghai")


def start():
    scheduler.add_job(
        tasks.fetch_daily_news,
        CronTrigger(hour=7, minute=0, timezone="Asia/Shanghai"),
        id="fetch_news",
    )
    scheduler.add_job(
        tasks.generate_scenario_job,
        CronTrigger(hour=8, minute=0, timezone="Asia/Shanghai"),
        id="scenario",
    )
    scheduler.add_job(
        tasks.fallback_job,
        CronTrigger(hour=21, minute=0, timezone="Asia/Shanghai"),
        id="fallback",
    )
    scheduler.add_job(
        tasks.weekly_job,
        CronTrigger(day_of_week=6, hour=20, minute=0, timezone="Asia/Shanghai"),
        id="weekly",
    )
    scheduler.add_job(
        tasks.backup_db,
        CronTrigger(hour=3, minute=0, timezone="Asia/Shanghai"),
        id="backup",
    )
    scheduler.start()
