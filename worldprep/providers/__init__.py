from dataclasses import dataclass
from functools import cached_property

from ..config import get_settings
from ..logging_setup import log
from ..retry import PermanentError


@dataclass
class Providers:
    mock: bool = False

    @cached_property
    def llm(self):
        if self.mock:
            from .mock import MockLLM

            return MockLLM()
        from .llm_claude import ClaudeProvider

        return ClaudeProvider()

    @property
    def research(self):
        return self.llm

    @cached_property
    def voice(self):
        s = get_settings()
        if self.mock or s.voice_provider == "mock":
            from .voice import MockVoice

            return MockVoice()
        from .voice import AzureVoice, EdgeVoice

        if s.voice_provider == "azure":
            try:
                return AzureVoice()
            except PermanentError as e:
                log.warning("voice_fallback", extra={"reason": str(e)})
        return EdgeVoice()

    @cached_property
    def stock_images(self):
        if self.mock:
            from .mock import MockImages

            return MockImages()
        from .images import PexelsImages, PixabayImages

        for cls in (PixabayImages, PexelsImages):
            try:
                return cls()
            except PermanentError as e:
                log.warning("stock_images_unavailable", extra={"provider": cls.name, "reason": str(e)})
        return None

    @cached_property
    def stock_videos(self):
        if self.mock:
            from .mock import MockVideos

            return MockVideos()
        from .images import PixabayVideos

        try:
            return PixabayVideos()
        except PermanentError as e:
            log.warning("stock_videos_unavailable", extra={"reason": str(e)})
            return None

    @cached_property
    def archive_images(self):
        if self.mock:
            from .mock import MockImages

            return MockImages(source="https://commons.wikimedia.org/wiki/File:Mock", license="Public domain")
        from .images import WikimediaImages

        return WikimediaImages()

    @cached_property
    def ai_images(self):
        if self.mock or get_settings().image_provider != "openai":
            return None
        from .images import OpenAIImages

        try:
            return OpenAIImages()
        except PermanentError as e:
            log.warning("ai_images_unavailable", extra={"reason": str(e)})
            return None

    @cached_property
    def slides(self):
        if self.mock:
            from .mock import MockSlides

            return MockSlides()
        from .slides import GeminiSlides

        try:
            return GeminiSlides()
        except PermanentError as e:
            log.warning("slides_unavailable", extra={"reason": str(e)})
            return None

    @cached_property
    def email(self):
        from .email_smtp import OutboxEmail, SMTPEmail

        if self.mock:
            return OutboxEmail()
        try:
            return SMTPEmail()
        except PermanentError as e:
            log.warning("email_outbox_fallback", extra={"reason": str(e)})
            return OutboxEmail()


def get_providers(mock: bool | None = None) -> Providers:
    return Providers(mock=get_settings().mock if mock is None else mock)
