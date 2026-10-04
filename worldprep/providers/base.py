from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class ResearchResult:
    text: str
    sources: list[dict] = field(default_factory=list)


@dataclass
class ImageResult:
    path: Path
    source: str
    creator: str = ""
    license: str = ""
    license_url: str = ""
    usage_rights: str = ""
    attribution_required: bool = False
    ai_generated: bool = False
    realistic: bool = False
    media_type: str = "image"


@dataclass
class VoiceResult:
    path: Path
    duration: float
    chars: int


class LLMProvider(Protocol):
    def json(self, task: str, system: str, prompt: str, schema: dict, episode_id: int | None = None,
             effort: str | None = None) -> Any: ...

    def text(self, task: str, system: str, prompt: str, episode_id: int | None = None,
             effort: str | None = None) -> str: ...


class ResearchProvider(Protocol):
    def research(self, task: str, system: str, prompt: str, episode_id: int | None = None) -> ResearchResult: ...


class ImageProvider(Protocol):
    name: str

    def get(self, query: str, out: Path, episode_id: int | None = None) -> ImageResult | None: ...


class VideoProvider(Protocol):
    name: str

    def get(self, query: str, out: Path, seconds: float, episode_id: int | None = None) -> ImageResult | None: ...


class VoiceProvider(Protocol):
    name: str

    def synthesize(self, text: str, out: Path, episode_id: int | None = None) -> VoiceResult: ...


class TranscriptionProvider(Protocol):
    def align(self, audio: Path, text: str) -> list[dict]: ...


class EmailProvider(Protocol):
    def send(self, to: str, subject: str, html: str, text: str, inline_images: dict[str, Path] | None = None) -> None: ...


class YouTubeProvider(Protocol):
    def upload(self, video: Path, meta: dict, contains_synthetic_media: bool) -> str: ...

    def status(self, video_id: str) -> dict: ...

    def set_thumbnail(self, video_id: str, image: Path) -> None: ...

    def upload_caption(self, video_id: str, srt: Path, language: str, name: str) -> None: ...

    def set_privacy(self, video_id: str, privacy: str) -> dict: ...
