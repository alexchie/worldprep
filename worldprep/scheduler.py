import html
import threading
from functools import wraps

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import get_settings
from .db import init_db
from .logging_setup import log
from .pipeline import produce, send_daily_email
from .providers import get_providers

_LOCK = threading.Lock()


def serial(fn):
    @wraps(fn)
    def inner():
        with _LOCK:
            fn()
    return inner


def _cron(hhmm: str, tz: str) -> CronTrigger:
    h, m = hhmm.split(":")
    return CronTrigger(hour=int(h), minute=int(m), timezone=tz)


def job_produce() -> None:
    try:
        eid = produce(get_providers())
        log.info("job_produce_done", extra={"episode_id": eid})
    except Exception as e:
        log.exception("job_produce_failed")
        if getattr(e, "notified", False):
            return
        try:
            get_providers().email.send(get_settings().email_to or "owner@localhost", "[世界先修課] 今日製作未啟動或失敗",
                                       f"<pre>{html.escape(str(e))[:3000]}</pre>", str(e)[:3000])
        except Exception:
            log.exception("job_produce_notify_failed")


def job_email() -> None:
    try:
        log.info("job_email", extra={"sent": send_daily_email(get_providers())})
    except Exception:
        log.exception("job_email_failed")


def main() -> None:
    """本機替代方案：電腦需在排程時段開機。"""
    init_db()
    cfg = get_settings()
    tz = cfg.timezone
    sched = BlockingScheduler(timezone=tz)
    opts = {"max_instances": 1, "coalesce": True, "misfire_grace_time": 3600}
    sched.add_job(serial(job_produce), _cron(cfg.schedule_produce, tz), id="produce", **opts)
    sched.add_job(serial(job_email), _cron(cfg.schedule_email, tz), id="email", **opts)
    log.info("scheduler_started", extra={"timezone": tz, "jobs": [j.id for j in sched.get_jobs()]})
    sched.start()
