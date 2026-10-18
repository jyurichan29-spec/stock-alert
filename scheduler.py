"""15分間隔のバックグラウンドスケジューラ."""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from . import db
from .checker import run_check
from .config import as_int

log = logging.getLogger("stockalert.scheduler")

_scheduler: BackgroundScheduler | None = None
JOB_ID = "stock-check"


def _job() -> None:
    try:
        result = run_check()
        log.info("定期チェック完了: %s", result["summary"])
    except Exception:  # noqa: BLE001
        log.exception("定期チェックでエラーが発生しました")


def start_scheduler(interval_minutes: int | None = None) -> BackgroundScheduler:
    """スケジューラを起動する（多重起動は無視）。"""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    if interval_minutes is None:
        interval_minutes = as_int(db.get_settings().get("poll_interval_minutes"), 15)

    sched = BackgroundScheduler(timezone="UTC", daemon=True)
    sched.add_job(
        _job,
        trigger=IntervalTrigger(minutes=max(1, interval_minutes)),
        id=JOB_ID,
        name=f"{interval_minutes}分ごとの株価チェック",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
        replace_existing=True,
    )
    sched.start()
    _scheduler = sched
    log.info("スケジューラ起動: %d 分間隔", interval_minutes)
    return sched


def restart_scheduler(interval_minutes: int) -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
    return start_scheduler(interval_minutes)


def scheduler_status() -> dict:
    if _scheduler is None or not _scheduler.running:
        return {"running": False, "next_run": None, "interval_minutes": None}
    job = _scheduler.get_job(JOB_ID)
    nxt = getattr(job, "next_run_time", None) if job else None
    trigger = getattr(job, "trigger", None) if job else None
    interval = getattr(getattr(trigger, "interval", None), "total_seconds", lambda: 0)()
    return {
        "running": True,
        "next_run": nxt.isoformat() if nxt else None,
        "interval_minutes": int(interval // 60) if interval else None,
    }
