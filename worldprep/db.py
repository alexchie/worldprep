from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import AuditLog, Base, Episode, StateTransition
from .states import State, check_transition

_engine = None
_Session = None


def engine():
    global _engine, _Session
    if _engine is None:
        url = get_settings().database_url
        if url.startswith("sqlite:///"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(url, future=True)
        if url.startswith("sqlite"):
            # 排程與審核伺服器是兩個行程：WAL 讓讀不擋寫，busy_timeout 避免 database is locked
            @event.listens_for(_engine, "connect")
            def _pragma(conn, _):
                cur = conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()
        _Session = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def init_db() -> None:
    Base.metadata.create_all(engine())


@contextmanager
def session() -> Session:
    engine()
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def transition(s: Session, ep: Episode, dst: State, note: str = "") -> None:
    src = State(ep.status)
    if src == dst:
        return
    check_transition(src, dst)
    ep.status = dst.value
    s.add(StateTransition(episode_id=ep.id, from_state=src.value, to_state=dst.value, note=note[:2000]))
    from .logging_setup import log

    log.info("state_transition", extra={"episode_id": ep.id, "from": src.value, "to": dst.value})


def audit(s: Session, action: str, episode_id: int | None = None, actor: str = "system", **detail) -> None:
    s.add(AuditLog(episode_id=episode_id, action=action, actor=actor, detail=detail or None))


def next_episode_number(s: Session) -> int:
    # 只看正數集數：測試用集數歸檔時改成負數，不影響正式編號
    return (s.scalar(select(func.max(Episode.episode_number)).where(Episode.episode_number > 0)) or 0) + 1
