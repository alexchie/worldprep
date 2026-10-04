import json
import shutil
from pathlib import Path
from typing import Any

from .config import get_settings

AREAS = ("raw", "research", "scripts", "audio", "assets", "video", "thumbnails", "subtitles", "final")


class StorageProvider:
    """以 episode 為單位的物件儲存。工作檔一律在本地路徑上處理，遠端 backend 只負責同步。"""

    def path(self, episode_id: int, area: str, name: str) -> Path:
        raise NotImplementedError

    def exists(self, episode_id: int, area: str, name: str) -> bool:
        return self.path(episode_id, area, name).exists()

    def write_json(self, episode_id: int, area: str, name: str, data: Any) -> Path:
        p = self.path(episode_id, area, name)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.sync(p)
        return p

    def read_json(self, episode_id: int, area: str, name: str) -> Any:
        return json.loads(self.path(episode_id, area, name).read_text(encoding="utf-8"))

    def write_text(self, episode_id: int, area: str, name: str, text: str) -> Path:
        p = self.path(episode_id, area, name)
        p.write_text(text, encoding="utf-8")
        self.sync(p)
        return p

    def sync(self, p: Path) -> None:
        pass

    def usage_bytes(self) -> int:
        return 0


class LocalStorage(StorageProvider):
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def path(self, episode_id: int, area: str, name: str) -> Path:
        if area not in AREAS:
            raise ValueError(f"unknown storage area {area}")
        p = self.root / f"ep{episode_id:04d}" / area / name
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def clear(self, episode_id: int, area: str) -> None:
        shutil.rmtree(self.root / f"ep{episode_id:04d}" / area, ignore_errors=True)

    def usage_bytes(self) -> int:
        return sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file()) if self.root.exists() else 0


class S3Storage(LocalStorage):
    """本地為工作快取，寫入後上傳到 S3 相容儲存（需 boto3 與 S3_BUCKET / S3_ENDPOINT_URL 環境變數）。"""

    def __init__(self, root: Path):
        super().__init__(root)
        import os

        import boto3

        self.bucket = os.environ["S3_BUCKET"]
        self.client = boto3.client("s3", endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None)

    def sync(self, p: Path) -> None:
        self.client.upload_file(str(p), self.bucket, p.relative_to(self.root).as_posix())


_storage: StorageProvider | None = None


def get_storage() -> StorageProvider:
    global _storage
    if _storage is None:
        s = get_settings()
        _storage = S3Storage(s.storage_root) if s.storage_backend == "s3" else LocalStorage(s.storage_root)
    return _storage
