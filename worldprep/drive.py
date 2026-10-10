"""Google Drive：成品交付 + GitHub Actions 每次執行之間的狀態同步（資料庫、工作檔、配樂）。"""
import io
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from sqlalchemy import select

from .brand import ep_label
from .config import ROOT, get_settings
from .logging_setup import log
from .retry import PermanentError, with_retry

FOLDER_MIME = "application/vnd.google-apps.folder"
SCOPES = ["https://www.googleapis.com/auth/drive"]
KEEP_WORK_DAYS = 7


def authorize_interactive() -> Path:
    """一次性在本機瀏覽器完成 OAuth，refresh token 存到 GOOGLE_TOKEN_FILE。"""
    from google_auth_oauthlib.flow import InstalledAppFlow

    s = get_settings()
    flow = InstalledAppFlow.from_client_secrets_file(str(s.google_client_secrets), SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    s.google_token_file.parent.mkdir(parents=True, exist_ok=True)
    s.google_token_file.write_text(creds.to_json(), encoding="utf-8")
    return s.google_token_file


def _credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    s = get_settings()
    if not s.google_token_file.exists():
        raise PermanentError("尚未授權 Google Drive：請執行 `python -m worldprep.cli google-auth`")
    creds = Credentials.from_authorized_user_file(str(s.google_token_file), SCOPES)
    if not creds.valid:
        try:
            creds.refresh(Request())
        except Exception as e:
            raise PermanentError(f"Google refresh token 失效，請重新 google-auth：{e}") from e
    return creds


def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


class Drive:
    def __init__(self):
        self.root = get_settings().drive_folder_id
        if not self.root:
            raise PermanentError("DRIVE_FOLDER_ID 未設定")
        self.api = build("drive", "v3", credentials=_credentials(), cache_discovery=False)
        self._folders: dict[tuple[str, str], str] = {}

    def _find(self, name: str, parent: str, folder: bool = False) -> str | None:
        q = f"name = '{_q(name)}' and '{parent}' in parents and trashed = false"
        if folder:
            q += f" and mimeType = '{FOLDER_MIME}'"
        r = self.api.files().list(q=q, fields="files(id)", pageSize=1, supportsAllDrives=True,
                                  includeItemsFromAllDrives=True).execute()
        return r["files"][0]["id"] if r["files"] else None

    @with_retry()
    def folder(self, *names: str) -> str:
        parent = self.root
        for name in names:
            key = (parent, name)
            if key not in self._folders:
                fid = self._find(name, parent, folder=True)
                if fid is None:
                    fid = self.api.files().create(body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent]},
                                                  fields="id", supportsAllDrives=True).execute()["id"]
                self._folders[key] = fid
            parent = self._folders[key]
        return parent

    @with_retry()
    def upload(self, path: Path, parent: str, name: str | None = None) -> str:
        name = name or path.name
        media = MediaFileUpload(str(path), resumable=path.stat().st_size > 5 * 1024 * 1024, chunksize=32 * 1024 * 1024)
        fid = self._find(name, parent)
        if fid:
            self.api.files().update(fileId=fid, media_body=media, supportsAllDrives=True).execute()
            return fid
        return self.api.files().create(body={"name": name, "parents": [parent]}, media_body=media, fields="id",
                                       supportsAllDrives=True).execute()["id"]

    @with_retry()
    def download(self, name: str, parent: str, dest: Path) -> bool:
        fid = self._find(name, parent)
        if not fid:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        buf = io.FileIO(str(dest), "wb")
        dl = MediaIoBaseDownload(buf, self.api.files().get_media(fileId=fid, supportsAllDrives=True), chunksize=32 * 1024 * 1024)
        done = False
        while not done:
            _, done = dl.next_chunk()
        buf.close()
        return True

    def list_names(self, parent: str) -> list[tuple[str, str]]:
        out, token = [], None
        while True:
            r = self.api.files().list(q=f"'{parent}' in parents and trashed = false", fields="nextPageToken, files(id, name)",
                                      pageToken=token, supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            out += [(f["id"], f["name"]) for f in r["files"]]
            token = r.get("nextPageToken")
            if not token:
                return out

    def delete(self, fid: str) -> None:
        self.api.files().delete(fileId=fid, supportsAllDrives=True).execute()


def _db_path() -> Path:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        raise PermanentError("Drive 狀態同步只支援 SQLite DATABASE_URL")
    return Path(url.removeprefix("sqlite:///"))


def _work_dir(eid: int) -> Path:
    return get_settings().storage_root / f"ep{eid:04d}"


def pull_state() -> None:
    """開工前：下載資料庫、配樂、未完成集數的工作檔。"""
    d = Drive()
    sysdir = d.folder("_system")
    db = _db_path()
    if d.download("worldprep.db", sysdir, db):
        log.info("drive_pulled_db")
    music_dir = get_settings().music_dir
    for fid, name in d.list_names(d.folder("_system", "music")):
        if not (music_dir / name).exists():
            d.download(name, d.folder("_system", "music"), music_dir / name)
    from .db import init_db, session
    from .models import Episode

    init_db()
    with session() as s:
        active = [e.id for e in s.scalars(select(Episode)) if not _expired(e)]
    work = d.folder("_system", "work")
    (ROOT / "data").mkdir(parents=True, exist_ok=True)
    for eid in active:
        tgz = ROOT / "data" / f"ep{eid:04d}.tar.gz"
        if not _work_dir(eid).exists() and d.download(tgz.name, work, tgz):
            with tarfile.open(tgz) as tf:
                if hasattr(tarfile, "data_filter"):
                    tf.extractall(get_settings().storage_root, filter="data")
                else:
                    tf.extractall(get_settings().storage_root)
            tgz.unlink()
            log.info("drive_pulled_work", extra={"episode_id": eid})


def _expired(ep) -> bool:
    """通知超過 KEEP_WORK_DAYS 天的集數不再保留工作檔（成品仍在 Drive 交付資料夾）。"""
    if ep.notified_at is None:
        return False
    at = ep.notified_at if ep.notified_at.tzinfo else ep.notified_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - at).days >= KEEP_WORK_DAYS


def _work_filter(ti, delivered: bool):
    """未完成集數：排除可重建的場景片段與合成檔；已交付集數：只保留文字、圖片等小檔（成品已在交付資料夾）。"""
    name = ti.name.replace("\\", "/")
    if delivered and name.endswith((".mp4", ".wav", ".mp3")):
        return None
    if name.endswith(".mp4") and ("/video/" in name or "/final/" in name):
        return None
    return ti


def push_state() -> None:
    """收工後：上傳資料庫與工作檔（排除可重建的場景片段）；過期集數的工作檔刪除。"""
    from .db import engine, session
    from .models import Episode

    d = Drive()
    sysdir = d.folder("_system")
    with engine().connect() as c:
        c.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    d.upload(_db_path(), sysdir, "worldprep.db")
    work = d.folder("_system", "work")
    (ROOT / "data").mkdir(parents=True, exist_ok=True)
    existing = dict((name, fid) for fid, name in d.list_names(work))
    with session() as s:
        eps = [(e.id, _expired(e), e.status in ("DELIVERED", "NOTIFIED")) for e in s.scalars(select(Episode))]
    for eid, expired, delivered in eps:
        name = f"ep{eid:04d}.tar.gz"
        if expired:
            if name in existing:
                d.delete(existing[name])
            continue
        src = _work_dir(eid)
        if not src.exists():
            continue
        tgz = ROOT / "data" / name
        with tarfile.open(tgz, "w:gz") as tf:
            tf.add(src, arcname=src.name, filter=lambda ti: _work_filter(ti, delivered))
        d.upload(tgz, work, name)
        tgz.unlink()
    log.info("drive_pushed_state")


DELIVERABLES = [
    ("final", "episode.mp4"), ("thumbnails", "thumbnail.jpg"), ("subtitles", "zh-Hant.srt"),
    ("final", "metadata.json"), ("final", "qa_report.json"), ("plan", "brief.json"),
    ("scripts", "script.txt"), ("scripts", "opening.md"), ("scripts", "storyboard.json"), ("research", "notes.md"),
    ("research", "claims.json"), ("research", "factcheck.json"), ("assets", "manifest.json"),
]


def deliver(p, eid: int) -> None:
    """成品交付到 <指定資料夾>/世界先修課/EP.xx_目的地/；mock 模式交付到本機 storage 的 drive_mock/。"""
    from .db import audit, session
    from .models import Episode
    from .storage import get_storage

    with session() as s:
        ep = s.get(Episode, eid)
        folder_name = f"{ep_label(ep.episode_number)}_{ep.destination}"
    st = get_storage()
    files = [st.path(eid, area, name) for area, name in DELIVERABLES]
    files = [f for f in files if f.exists()]
    candidates = sorted(st.path(eid, "thumbnails", "x").parent.glob("candidate_*.jpg"))
    if get_settings().mock:
        import shutil

        target = get_settings().storage_root / "drive_mock" / "世界先修課" / folder_name
        (target / "thumbnail_candidates").mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy(f, target / f.name)
        for f in candidates:
            shutil.copy(f, target / "thumbnail_candidates" / f.name)
        url = target.resolve().as_uri()
    else:
        d = Drive()
        target = d.folder("世界先修課", folder_name)
        for f in files:
            d.upload(f, target)
        for f in candidates:
            d.upload(f, d.folder("世界先修課", folder_name, "thumbnail_candidates"))
        url = f"https://drive.google.com/drive/folders/{target}"
    with session() as s:
        ep = s.get(Episode, eid)
        ep.drive_folder_url, ep.delivered_at = url, datetime.now(timezone.utc)
        audit(s, "delivered", eid, folder=folder_name)
    log.info("drive_delivered", extra={"episode_id": eid, "folder": folder_name})


def upload_music(path: Path) -> None:
    """授權配樂放 Drive，不放公開 repo。"""
    d = Drive()
    d.upload(path, d.folder("_system", "music"))
