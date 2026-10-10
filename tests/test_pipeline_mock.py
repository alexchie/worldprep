import re
from pathlib import Path

from worldprep.config import get_settings
from worldprep.db import init_db, session
from worldprep.models import Episode
from worldprep.pipeline import produce, regenerate, advance, send_daily_email
from worldprep.providers import get_providers
from worldprep.storage import get_storage


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
        # 製作人的原標題被腳本審查判定不受支持，換成修正版；不合規的「旅遊攻略」不會成為備選
        assert ep.title.startswith("東京為什麼能成為世界之都？從江戶")
        meta = get_storage().read_json(eid, "final", "metadata.json")
        assert meta["title"] == ep.title and meta["title_alternates"]
        assert not any("旅遊攻略" in t for t in meta["title_alternates"])
        assert ep.drive_folder_url.startswith("file:")
        folder = get_settings().storage_root / "drive_mock" / "世界先修課" / f"EP.{ep.episode_number:02d}_東京"
        title, ep_number = ep.title, ep.episode_number
    for name in ("episode.mp4", "short.mp4", "short_cover.jpg", "thumbnail.jpg", "zh-Hant.srt", "metadata.json", "qa_report.json"):
        assert any(folder.rglob(name)), name

    before = len(_outbox())
    assert send_daily_email(p) == [eid]
    # 中文版與英文版（Beyond Travel）各一封
    assert len(_outbox()) == before + 2
    mails = [f.read_text(encoding="utf-8") for f in _outbox()[before:]]
    mail = next(m for m in mails if "上傳檢查清單" in m)
    en_mail = next(m for m in mails if "Upload checklist" in m)
    assert title in mail and "YouTube 說明" in mail and "#世界先修課" in mail
    assert "標籤" not in mail and "#Tokyo" in mail  # 標籤併入說明欄、以 # 開頭
    assert "Instagram 貼文" in mail and "Threads 貼文" in mail and "YouTube Shorts 標題" in mail
    assert "Beyond Travel EP." in en_mail and "Instagram post" in en_mail and "Threads post" in en_mail
    en_folder = get_settings().storage_root / "drive_mock" / "Beyond Travel" / f"EP.{ep_number:02d}_Tokyo"
    for name in ("episode.mp4", "short.mp4", "short_cover.jpg", "thumbnail.jpg", "en.srt", "metadata.json"):
        assert any(en_folder.rglob(name)), name
    with session() as s:
        assert s.get(Episode, eid).status == "NOTIFIED"

    # 同一天排程再觸發：已寄過信，不再寄「今日沒有新影片」
    assert send_daily_email(p) == []
    assert len(_outbox()) == before + 2

    regenerate(p, eid, "title", "更有懸念")
    advance(p, eid)
    with session() as s:
        assert s.get(Episode, eid).status == "DELIVERED"
