from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    timezone: str = "Asia/Taipei"
    database_url: str = f"sqlite:///{(ROOT / 'data' / 'worldprep.db').as_posix()}"
    storage_backend: str = "local"
    storage_root: Path = ROOT / "storage"
    log_dir: Path = ROOT / "logs"
    target_video_length_minutes: int = 9
    video_min_minutes: float = 8.0
    video_max_minutes: float = 10.0
    narration_chars_per_minute: int = 260

    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5-5"
    llm_sonnet_model: str = "claude-sonnet-5-5"
    llm_fast_model: str = "claude-haiku-4-5"
    batch_enabled: bool = True
    batch_wait_minutes: int = 30
    llm_effort: str = "high"

    voice_provider: str = "edge"
    azure_speech_key: str = ""
    azure_speech_region: str = "eastasia"
    voice_name: str = "zh-TW-HsiaoChenNeural"

    pexels_api_key: str = ""
    pixabay_api_key: str = ""
    image_provider: str = "none"
    openai_api_key: str = ""
    openai_image_model: str = "gpt-image-1"
    gemini_api_key: str = ""
    slide_model: str = "gemini-nano-banana-2.1"
    slide_price_usd: float = 0.0504  # 2K，即時價；Batch 半價
    slide_batch_wait_minutes: int = 60
    cover_model: str = "gemini-3-pro-image"  # 封面字多、要求逐字正確，用 Nano Banana Pro
    cover_price_usd: float = 0.134
    cover_attempts: int = 3
    hook_min_seconds: float = 15.0
    hook_max_seconds: float = 23.0
    brand_pause_seconds: float = 0.75  # 品牌台詞前、後各留的空白（Hook → 空白 → 品牌 → 空白 → 正文）
    brand_line: str = "世界先修課，跟著我們一起看懂世界再出發"  # 與正文同語速、長度不設限

    music_dir: Path = ROOT / "music"

    google_client_secrets: Path = ROOT / "secrets" / "client_secret.json"
    google_token_file: Path = ROOT / "secrets" / "google_token.json"
    drive_folder_id: str = ""

    imap_host: str = "imap.gmail.com"
    topic_fallback: str = "skip"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_to: str = ""

    subtitle_font: str = "Noto Sans CJK TC" if __import__("sys").platform != "win32" else "Microsoft JhengHei"

    daily_budget_usd: float = 15.0
    episode_budget_usd: float = 12.0

    schedule_produce: str = "18:00"
    schedule_email: str = "08:00"

    font_bold: str = ""
    font_regular: str = ""
    ffmpeg_path: str = ""

    mock: bool = False

    @property
    def target_chars(self) -> int:
        return self.target_video_length_minutes * self.effective_chars_per_minute

    @property
    def effective_chars_per_minute(self) -> int:
        import json

        cal = ROOT / "data" / "calibration.json"
        if cal.exists():
            return int(json.loads(cal.read_text(encoding="utf-8")).get("chars_per_minute", self.narration_chars_per_minute))
        return self.narration_chars_per_minute


@lru_cache
def get_settings() -> Settings:
    return Settings()
