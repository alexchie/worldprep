import html
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select, update

from . import drive
from .agents import edit, english, factcheck, metadata, notify, qa, research, script, shorts, storyboard, thumbnail, visual, voice
from .agents.topic import full_titles, read_brief, retitle, select_topic
from .db import audit, session, transition
from .logging_setup import log
from .models import Episode, ProductionJob
from .providers import Providers
from .retry import PermanentError
from .states import State as S
from .storage import get_storage

STAGE_ATTEMPTS = 2


def _deliver_all(p, eid):
    """中文版先交付；英文版接著做並交付到 Beyond Travel 資料夾。英文版失敗只寄通知，不擋中文版。"""
    drive.deliver(p, eid)
    from .config import get_settings

    if not get_settings().english_enabled:
        return
    try:
        english.run(p, eid)
        drive.deliver_en(eid)
    except Exception as e:
        log.error("english_failed", extra={"episode_id": eid, "err": str(e)[:500]})
        notify_failure(p, eid, "english", e)


def _render_all(p, eid):
    # 封面先做：影片第一幀要承接封面主視覺
    # 已交付集數的雲端存檔不含配音檔；重做時先補回缺少的配音（已存在的會略過）
    voice.run(p, eid)
    thumbnail.run(p, eid)
    edit.run(p, eid)
    shorts.run(p, eid)
    metadata.run(p, eid)


@dataclass
class Stage:
    name: str
    start: S
    working: S | None
    done: S | None
    fn: Callable


STAGES = [
    Stage("research", S.IDEA, S.RESEARCHING, S.RESEARCH_COMPLETE, research.run),
    Stage("factcheck", S.RESEARCH_COMPLETE, S.FACT_CHECKING, S.FACT_CHECK_COMPLETE, factcheck.run),
    Stage("script", S.FACT_CHECK_COMPLETE, S.SCRIPTING, S.SCRIPT_COMPLETE, script.run),
    Stage("storyboard", S.SCRIPT_COMPLETE, S.STORYBOARDING, None, storyboard.run),
    Stage("assets", S.STORYBOARDING, S.ASSET_GENERATION, None, visual.run),
    Stage("voice", S.ASSET_GENERATION, S.VOICE_GENERATION, None, voice.run),
    Stage("render", S.VOICE_GENERATION, S.RENDERING, None, _render_all),
    Stage("qa", S.RENDERING, S.QA, S.READY_FOR_DELIVERY, qa.run),
    Stage("deliver", S.READY_FOR_DELIVERY, None, S.DELIVERED, _deliver_all),
]


class QAFailed(Exception):
    pass


def _job_done(s, eid: int, name: str) -> bool:
    return s.scalar(select(ProductionJob.id).where(ProductionJob.episode_id == eid, ProductionJob.job_type == name,
                                                   ProductionJob.status == "done")) is not None


def _next_stage(s, ep: Episode) -> Stage | None:
    st = S(ep.status)
    for stage in STAGES:
        if stage.working == st:
            return _after(stage) if _job_done(s, ep.id, stage.name) else stage
        if stage.start == st:
            return stage
    return None


def _after(stage: Stage) -> Stage | None:
    i = STAGES.index(stage)
    return STAGES[i + 1] if i + 1 < len(STAGES) else None


def run_stage(p: Providers, eid: int, stage: Stage) -> None:
    from . import costs
    from .config import get_settings

    budget = get_settings().episode_budget_usd
    if stage.name != "deliver" and costs.episode_total(eid) >= budget:
        with session() as s:
            s.get(Episode, eid).needs_human_review = True
        raise PermanentError(f"本集支出已達單集預算 {budget} USD，停在 {stage.name} 前。確認後調高 EPISODE_BUDGET_USD 再 resume")
    with session() as s:
        ep = s.get(Episode, eid)
        if stage.working and ep.status != stage.working.value:
            transition(s, ep, stage.working)
        attempt = 1 + len(list(s.scalars(select(ProductionJob.id).where(ProductionJob.episode_id == eid,
                                                                        ProductionJob.job_type == stage.name,
                                                                        ProductionJob.status == "failed"))))
        job = ProductionJob(episode_id=eid, job_type=stage.name, attempt=attempt)
        s.add(job)
        s.flush()
        job_id = job.id
    log.info("stage_start", extra={"episode_id": eid, "stage": stage.name, "attempt": attempt})
    started = datetime.now(timezone.utc)
    try:
        result = stage.fn(p, eid)
        if stage.name == "qa" and result is False:
            raise QAFailed("QA 未通過，詳見 final/qa_report.json")
    except Exception as e:
        with session() as s:
            j = s.get(ProductionJob, job_id)
            j.status, j.completed_at, j.error = "failed", datetime.now(timezone.utc), f"{e}\n{traceback.format_exc()[-3000:]}"
        log.error("stage_failed", extra={"episode_id": eid, "stage": stage.name, "err": str(e)[:500]})
        raise
    with session() as s:
        j = s.get(ProductionJob, job_id)
        j.status, j.completed_at = "done", datetime.now(timezone.utc)
        ep = s.get(Episode, eid)
        if stage.done:
            transition(s, ep, stage.done)
    log.info("stage_done", extra={"episode_id": eid, "stage": stage.name,
                                  "seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 1)})


def _fail(eid: int, err: Exception) -> None:
    with session() as s:
        ep = s.get(Episode, eid)
        if ep.status != S.FAILED.value:
            ep.failed_from = ep.status
            ep.last_error = str(err)[:4000]
            transition(s, ep, S.FAILED, str(err)[:500])


def resume_failed(eid: int) -> None:
    with session() as s:
        ep = s.get(Episode, eid)
        if ep.status == S.FAILED.value and ep.failed_from:
            transition(s, ep, S(ep.failed_from), "resume")
            ep.last_error = ""


def advance(p: Providers, eid: int, stop_after: str = "deliver") -> str:
    """從目前狀態往前推進到 stop_after（含），失敗時記錄並保留產物以便續跑。"""
    resume_failed(eid)
    stop_idx = [s.name for s in STAGES].index(stop_after)
    while True:
        with session() as s:
            stage = _next_stage(s, s.get(Episode, eid))
        if stage is None or STAGES.index(stage) > stop_idx:
            break
        last: Exception | None = None
        for _ in range(STAGE_ATTEMPTS):
            try:
                run_stage(p, eid, stage)
                last = None
                break
            except (PermanentError, QAFailed) as e:
                last = e
                break
            except Exception as e:
                last = e
        if last:
            _fail(eid, last)
            notify_failure(p, eid, stage.name, last)
            last.notified = True
            raise last
    with session() as s:
        return s.get(Episode, eid).status


def notify_failure(p: Providers, eid: int, stage: str, err: Exception) -> None:
    from .config import get_settings

    try:
        with session() as s:
            ep = s.get(Episode, eid)
            title = f"[世界先修課] EP.{ep.episode_number:02d} {stage} 失敗"
        p.email.send(get_settings().email_to or "owner@localhost", title,
                     f"<p>階段 <b>{stage}</b> 失敗：</p><pre>{html.escape(str(err))[:3000]}</pre><p>修正後執行 <code>python -m worldprep.cli resume {eid}</code></p>",
                     f"{stage} failed: {err}")
    except Exception as e:
        log.error("failure_notification_failed", extra={"episode_id": eid, "err": str(e)})


def active_episode() -> int | None:
    done = [S.DELIVERED.value, S.NOTIFIED.value]
    with session() as s:
        ep = s.scalar(select(Episode).where(Episode.status.not_in(done)).order_by(Episode.id))
        return ep.id if ep else None


def _made_tonight() -> bool:
    """製作時段（SCHEDULE_PRODUCE 起算）內已經開過新集數，就不再開第二集；排程每小時檢查一次。"""
    from datetime import time as dtime, timedelta
    from zoneinfo import ZoneInfo

    from .config import get_settings

    cfg = get_settings()
    tz = ZoneInfo(cfg.timezone)
    now = datetime.now(tz)
    h, m = map(int, cfg.schedule_produce.split(":"))
    start = datetime.combine(now.date(), dtime(h, m), tz)
    if now < start:
        start -= timedelta(days=1)
    with session() as s:
        latest = s.scalar(select(Episode.created_at).order_by(Episode.created_at.desc()))
    if latest is None:
        return False
    latest = latest if latest.tzinfo else latest.replace(tzinfo=timezone.utc)
    return latest >= start


def produce(p: Providers, destination: str | None = None) -> int | None:
    """每日製作：續跑未完成（含失敗）的集數；否則依序採用 指定目的地 → email 指定主題 → 選題池（TOPIC_FALLBACK=auto）。"""
    from . import costs
    from .agents import inbox
    from .agents.topic import select_topic_from_request
    from .config import get_settings

    cfg = get_settings()
    eid = active_episode()
    if eid is None and not destination and _made_tonight():
        log.info("already_produced_tonight")
        return None
    # 預算檢查放在「確定今晚還有事要做」之後：當晚已做完時，排程的例行檢查不應因當天支出而報錯
    if costs.today_total() >= cfg.daily_budget_usd:
        raise PermanentError(f"今日支出已達預算 {cfg.daily_budget_usd} USD，停止製作，需人工確認")
    if eid is None and destination:
        eid = select_topic(p, destination) if cfg.mock else select_topic_from_request(p, None, destination)
    if eid is None and not cfg.mock:
        try:
            inbox.fetch_requests()
        except Exception as e:
            log.warning("topic_inbox_failed", extra={"err": str(e)[:300]})
        req = inbox.next_request()
        if req:
            eid = select_topic_from_request(p, req.id, req.text)
    if eid is None:
        if cfg.topic_fallback != "auto":
            log.info("no_topic_requested_skip")
            return None
        eid = select_topic(p)
    advance(p, eid, "deliver")
    return eid


def _supersede_jobs(s, eid: int, from_stage: str) -> None:
    names = [st.name for st in STAGES[[x.name for x in STAGES].index(from_stage):]]
    s.execute(update(ProductionJob).where(ProductionJob.episode_id == eid, ProductionJob.job_type.in_(names),
                                          ProductionJob.status == "done").values(status="superseded"))


def regenerate(p: Providers, eid: int, target: str, feedback: str = "") -> None:
    """看完不滿意時手動重做：title | thumbnail | script | scenes | video。重做後重新交付並在下一封 08:00 信通知。"""
    st = get_storage()
    with session() as s:
        ep = s.get(Episode, eid)
        transition(s, ep, S.REGENERATING, f"target={target}")
        audit(s, "regenerate", eid, target=target, feedback=feedback[:2000])

    if target in ("title", "thumbnail"):
        if target == "title":
            retitle(p, eid, feedback)
            with session() as s:
                ep = s.get(Episode, eid)
                ep.title = full_titles(read_brief(eid), ep.episode_number)[0]
        thumbnail.run(p, eid, feedback, force=True)
        # 影片第一幀是封面，封面或標題換了就重新剪輯（場景片段會沿用）
        for area, name in (("final", "episode.mp4"), ("final", "short.mp4"), ("final", "metadata.json"), ("video", "timeline.json")):
            st.path(eid, area, name).unlink(missing_ok=True)
        # 英文版的封面、成品與說明也重做（沿用英文旁白、投影片與配音）
        en = english.en_root(eid) / f"ep{eid:04d}"
        for rel in ("thumbnails/thumbnail.jpg", "final/episode.mp4", "final/short.mp4", "final/metadata.json", "video/timeline.json"):
            (en / rel).unlink(missing_ok=True)
        restart, from_stage = S.VOICE_GENERATION, "render"
    else:
        for area in ("video", "final", "thumbnails", "subtitles"):
            st.clear(eid, area)
        # 內容變了，英文版要跟著重做（交付時自動重跑）
        import shutil

        shutil.rmtree(english.en_root(eid), ignore_errors=True)
        if target == "scenes":
            import re

            ids = set(re.findall(r"s\d{3}", feedback))
            visual.run(p, eid, only_scenes=ids or None)
            restart, from_stage = S.ASSET_GENERATION, "voice"
        else:
            # 新腳本的場景編號會沿用 s001…，必須清掉舊素材與旁白，否則會配到舊畫面
            for area in ("scripts", "assets", "audio"):
                st.clear(eid, area)
            script.run(p, eid, feedback)
            restart, from_stage = S.SCRIPT_COMPLETE, "storyboard"
    with session() as s:
        ep = s.get(Episode, eid)
        _supersede_jobs(s, eid, from_stage)
        transition(s, ep, restart, "regeneration restart")


def send_daily_email(p: Providers) -> list[int]:
    return notify.send_daily(p)
