import itertools
import shutil
from datetime import datetime, timedelta, timezone

import pytest

from worldprep import drive
from worldprep.config import get_settings
from worldprep.db import init_db, session
from worldprep.models import Episode

_ids = itertools.count(1)


class FakeDrive:
    files: dict = {}

    def __init__(self):
        self.root = "root"

    def folder(self, *names):
        return "/".join(("root",) + names)

    def upload(self, path, parent, name=None):
        FakeDrive.files[(parent, name or path.name)] = path.read_bytes()

    def download(self, name, parent, dest):
        if (parent, name) not in FakeDrive.files:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(FakeDrive.files[(parent, name)])
        return True

    def list_names(self, parent):
        return [(f"{p}|{n}", n) for (p, n) in FakeDrive.files if p == parent]

    def delete(self, fid):
        p, n = fid.split("|", 1)
        FakeDrive.files.pop((p, n), None)


@pytest.fixture(autouse=True)
def fake_drive(monkeypatch):
    FakeDrive.files = {}
    monkeypatch.setattr(drive, "Drive", FakeDrive)
    init_db()
    yield
    with session() as s:
        for ep in s.query(Episode).filter(Episode.episode_number > 900):
            shutil.rmtree(get_settings().storage_root / f"ep{ep.id:04d}", ignore_errors=True)
            s.delete(ep)


def _episode(status: str, notified_days_ago: int | None = None) -> int:
    with session() as s:
        ep = Episode(episode_number=900 + next(_ids), destination="測試", status=status,
                     notified_at=None if notified_days_ago is None else datetime.now(timezone.utc) - timedelta(days=notified_days_ago))
        s.add(ep)
        s.flush()
        return ep.id


def _make_work(eid: int):
    work = get_settings().storage_root / f"ep{eid:04d}"
    (work / "video").mkdir(parents=True, exist_ok=True)
    (work / "video" / "s001.mp4").write_bytes(b"clip")
    (work / "scripts").mkdir(parents=True, exist_ok=True)
    (work / "scripts" / "script.json").write_text("{}", encoding="utf-8")
    return work


def test_work_dir_roundtrip_excludes_scene_clips():
    eid = _episode("RENDERING")
    work = _make_work(eid)
    drive.push_state()
    shutil.rmtree(work)
    drive.pull_state()
    assert (work / "scripts" / "script.json").exists()
    assert not (work / "video" / "s001.mp4").exists()


def test_old_notified_work_is_dropped():
    eid = _episode("NOTIFIED", notified_days_ago=drive.KEEP_WORK_DAYS + 1)
    _make_work(eid)
    FakeDrive.files[("root/_system/work", f"ep{eid:04d}.tar.gz")] = b"old"
    drive.push_state()
    assert ("root/_system/work", f"ep{eid:04d}.tar.gz") not in FakeDrive.files
