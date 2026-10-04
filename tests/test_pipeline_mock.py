import re
from pathlib import Path

from worldprep.config import get_settings
from worldprep.db import init_db, session
from worldprep.models import Episode
from worldprep.pipeline import produce, regenerate, advance, send_daily_email
from worldprep.providers import get_providers


def _outbox() -> list[Path]:
    return sorted((get_settings().storage_root / "outbox").glob("*.html"))


def test_full_mock_episode_delivery_and_daily_email():
    init_db()
    p = get_providers(mock=True)
    eid = produce(p, "東京")
    with session() as s:
        ep = s.get(Episode, eid)
        assert ep.status == "DELIVERED" and ep.qa_status == "PASS"
        assert re.search(r"｜世界先修課 EP\.\d{2}$", ep.title)
        assert ep.drive_folder_url.startswith("file:")
        folder = get_settings().storage_root / "drive_mock" / "世界先修課" / f"EP.{ep.episode_number:02d}_東京"
        title = ep.title
    for name in ("episode.mp4", "thumbnail.jpg", "zh-Hant.srt", "metadata.json", "qa_report.json"):
        assert any(folder.rglob(name)), name

    before = len(_outbox())
    assert send_daily_email(p) == [eid]
    mail = _outbox()[-1].read_text(encoding="utf-8")
    assert len(_outbox()) == before + 1
    assert title in mail and "YouTube 說明" in mail and "上傳檢查清單" in mail and "#世界先修課" in mail
    with session() as s:
        assert s.get(Episode, eid).status == "NOTIFIED"

    assert send_daily_email(p) == []
    assert "今日沒有新影片" in _outbox()[-1].name or "沒有新影片" in _outbox()[-1].read_text(encoding="utf-8")

    regenerate(p, eid, "title", "更有懸念")
    advance(p, eid)
    with session() as s:
        assert s.get(Episode, eid).status == "DELIVERED"
