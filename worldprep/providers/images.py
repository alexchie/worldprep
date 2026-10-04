import base64
from pathlib import Path

import httpx

from .. import costs
from ..config import get_settings
from ..retry import PermanentError, with_retry
from .base import ImageResult


class PexelsImages:
    """Pexels 授權素材（Pexels License：可商用、免署名，但不得單獨轉售未修改素材）。"""

    name = "pexels"
    LICENSE_URL = "https://www.pexels.com/license/"

    def __init__(self):
        self.key = get_settings().pexels_api_key
        if not self.key:
            raise PermanentError("PEXELS_API_KEY 未設定")
        self._used: set[int] = set()

    @with_retry()
    def get(self, query: str, out: Path, episode_id: int | None = None) -> ImageResult | None:
        r = httpx.get("https://api.pexels.com/v1/search", headers={"Authorization": self.key},
                      params={"query": query, "orientation": "landscape", "size": "large", "per_page": 15}, timeout=30)
        if r.status_code == 401:
            raise PermanentError("Pexels API key invalid")
        r.raise_for_status()
        photos = [p for p in r.json().get("photos", []) if p["id"] not in self._used and p["width"] >= 1920]
        if not photos:
            return None
        p = photos[0]
        self._used.add(p["id"])
        img = httpx.get(p["src"]["original"], timeout=120, follow_redirects=True)
        img.raise_for_status()
        out = out.with_suffix(".jpg")
        out.write_bytes(img.content)
        costs.record(episode_id, "pexels", "stock_image", 0.0, photo_id=p["id"])
        return ImageResult(path=out, source=p["url"], creator=p.get("photographer", ""), license="Pexels License",
                           license_url=self.LICENSE_URL, usage_rights="commercial use, no attribution required",
                           attribution_required=False)


class OpenAIImages:
    """AI 生成圖像（歷史重建、概念畫面）。寫實風格會觸發 YouTube 合成內容揭露。"""

    name = "openai"

    def __init__(self):
        s = get_settings()
        if not s.openai_api_key:
            raise PermanentError("OPENAI_API_KEY 未設定")
        self.key, self.model = s.openai_api_key, s.openai_image_model

    @with_retry(attempts=3)
    def get(self, query: str, out: Path, episode_id: int | None = None, realistic: bool = True) -> ImageResult | None:
        r = httpx.post("https://api.openai.com/v1/images/generations",
                       headers={"Authorization": f"Bearer {self.key}"},
                       json={"model": self.model, "prompt": query, "size": "1536x1024", "n": 1}, timeout=300)
        if r.status_code in (400, 401, 403):
            raise PermanentError(f"OpenAI images {r.status_code}: {r.text[:300]}")
        r.raise_for_status()
        out = out.with_suffix(".png")
        out.write_bytes(base64.b64decode(r.json()["data"][0]["b64_json"]))
        costs.record(episode_id, "openai", "image_generation", costs.IMAGE_PER_IMAGE["openai"])
        return ImageResult(path=out, source=f"openai:{self.model}", creator="AI generated", license="Generated",
                           license_url="https://openai.com/policies/terms-of-use", usage_rights="owned output",
                           ai_generated=True, realistic=realistic)


class PixabayImages:
    """Pixabay 照片（Pixabay Content License：可商用、免署名；須下載保存，不可熱連結）。"""

    name = "pixabay"
    LICENSE_URL = "https://pixabay.com/service/license-summary/"

    def __init__(self):
        self.key = get_settings().pixabay_api_key
        if not self.key:
            raise PermanentError("PIXABAY_API_KEY 未設定")
        self._used: set[int] = set()

    def _search(self, endpoint: str, query: str, **params) -> list[dict]:
        r = httpx.get(f"https://pixabay.com/api/{endpoint}", timeout=30,
                      params={"key": self.key, "q": query[:100], "safesearch": "true", "per_page": 20, **params})
        if r.status_code in (400, 401, 403):
            raise PermanentError(f"Pixabay {r.status_code}: {r.text[:200]}")
        r.raise_for_status()
        return [h for h in r.json().get("hits", []) if h["id"] not in self._used]

    @with_retry()
    def get(self, query: str, out: Path, episode_id: int | None = None) -> ImageResult | None:
        hits = self._search("", query, image_type="photo", orientation="horizontal", min_width=1920)
        if not hits:
            return None
        h = hits[0]
        self._used.add(h["id"])
        img = httpx.get(h["largeImageURL"], timeout=120, follow_redirects=True)
        img.raise_for_status()
        out = out.with_suffix(".jpg")
        out.write_bytes(img.content)
        costs.record(episode_id, "pixabay", "stock_image", 0.0, pixabay_id=h["id"])
        return ImageResult(path=out, source=h["pageURL"], creator=h.get("user", ""), license="Pixabay Content License",
                           license_url=self.LICENSE_URL, usage_rights="commercial use, no attribution required")


class PixabayVideos(PixabayImages):
    """Pixabay 影片片段（1080p 優先）。"""

    name = "pixabay_video"

    @with_retry()
    def get(self, query: str, out: Path, episode_id: int | None = None, min_seconds: float = 5) -> ImageResult | None:
        hits = [h for h in self._search("videos/", query, video_type="film") if h.get("duration", 0) >= min_seconds]
        for h in hits:
            v = h["videos"].get("medium") or {}
            if v.get("width", 0) < 1920:
                v = h["videos"].get("large") or {}
            if v.get("url") and v.get("width", 0) >= 1920:
                break
        else:
            return None
        self._used.add(h["id"])
        out = out.with_suffix(".mp4")
        with httpx.stream("GET", v["url"], timeout=300, follow_redirects=True) as r:
            r.raise_for_status()
            with open(out, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        costs.record(episode_id, "pixabay", "stock_video", 0.0, pixabay_id=h["id"])
        return ImageResult(path=out, source=h["pageURL"], creator=h.get("user", ""), license="Pixabay Content License",
                           license_url=self.LICENSE_URL, usage_rights="commercial use, no attribution required",
                           media_type="video")


class WikimediaImages:
    """Wikimedia Commons 歷史照片、古地圖、畫作。只採用公有領域、CC0 與 CC BY（不含 SA/NC/ND），CC BY 自動列入說明欄署名。"""

    name = "wikimedia"
    API = "https://commons.wikimedia.org/w/api.php"
    HEADERS = {"User-Agent": "worldprep/1.0 (https://alexchie.github.io/)"}

    def __init__(self):
        self._used: set[str] = set()

    @staticmethod
    def allowed(license_name: str) -> tuple[bool, bool]:
        """回傳 (可用, 需署名)。"""
        import re

        lic = license_name.lower().strip()
        tokens = set(re.split(r"[^a-z0-9]+", lic))
        if tokens & {"sa", "nc", "nd", "gfdl"} or "fair use" in lic:
            return False, False
        if "public domain" in lic or lic.startswith("pd") or "cc0" in lic or "no restrictions" in lic:
            return True, False
        if lic.startswith("cc by"):
            return True, True
        return False, False

    @with_retry()
    def get(self, query: str, out: Path, episode_id: int | None = None) -> ImageResult | None:
        import re

        r = httpx.get(self.API, headers=self.HEADERS, timeout=30, params={
            "action": "query", "format": "json", "generator": "search", "gsrsearch": f"{query} filetype:bitmap",
            "gsrnamespace": 6, "gsrlimit": 15, "prop": "imageinfo", "iiprop": "url|extmetadata|size", "iiurlwidth": 2400})
        r.raise_for_status()
        pages = sorted(r.json().get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
        for p in pages:
            info = (p.get("imageinfo") or [{}])[0]
            meta = info.get("extmetadata", {})
            lic = meta.get("LicenseShortName", {}).get("value", "")
            ok, attribution = self.allowed(lic)
            if not ok or p["title"] in self._used or info.get("width", 0) < 1000:
                continue
            img = httpx.get(info.get("thumburl") or info["url"], headers=self.HEADERS, timeout=120, follow_redirects=True)
            img.raise_for_status()
            self._used.add(p["title"])
            out = out.with_suffix(".jpg")
            out.write_bytes(img.content)
            artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()[:200] or "Unknown"
            costs.record(episode_id, "wikimedia", "archive_image", 0.0, title=p["title"])
            return ImageResult(path=out, source=info.get("descriptionurl", ""), creator=artist, license=lic,
                               license_url=meta.get("LicenseUrl", {}).get("value", ""),
                               usage_rights="commercial use allowed", attribution_required=attribution)
        return None
