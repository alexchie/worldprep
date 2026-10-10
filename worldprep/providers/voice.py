import asyncio
from pathlib import Path
from xml.sax.saxutils import escape

import httpx

from .. import costs
from ..config import get_settings
from ..render.ffmpeg import media_duration, run_ffmpeg
from ..retry import PermanentError, with_retry
from .base import VoiceResult

_EDGE = "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.03"
TRIM_SILENCE = f"{_EDGE},areverse,{_EDGE},areverse"
MAX_CHARS_PER_SECOND = 9  # 正常旁白約每秒 4–5 字


class AzureVoice:
    """Azure Speech REST TTS（官方 zh-TW 神經語音，SSML 控制語速與停頓）。"""

    name = "azure"

    def __init__(self):
        s = get_settings()
        if not s.azure_speech_key:
            raise PermanentError("AZURE_SPEECH_KEY 未設定")
        self.key, self.region, self.voice = s.azure_speech_key, s.azure_speech_region, s.voice_name

    @with_retry()
    def synthesize(self, text: str, out: Path, episode_id: int | None = None, rate: str = "-3%") -> VoiceResult:
        ssml = (
            '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="zh-TW">'
            f'<voice name="{self.voice}"><mstts:express-as style="narration-professional">'
            f'<prosody rate="{rate}">{escape(text)}</prosody></mstts:express-as></voice></speak>'
        )
        r = httpx.post(
            f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1",
            headers={"Ocp-Apim-Subscription-Key": self.key, "Content-Type": "application/ssml+xml",
                     "X-Microsoft-OutputFormat": "riff-48khz-16bit-mono-pcm", "User-Agent": "worldprep"},
            content=ssml.encode("utf-8"), timeout=120,
        )
        if r.status_code in (400, 401, 403):
            raise PermanentError(f"Azure TTS {r.status_code}: {r.text[:200]}")
        r.raise_for_status()
        out = out.with_suffix(".wav")
        out.write_bytes(r.content)
        costs.record(episode_id, "azure", "tts", len(text) * costs.TTS_PER_1M_CHARS["azure"] / 1e6, chars=len(text))
        return VoiceResult(out, media_duration(out), len(text))


class EdgeVoice:
    """Edge TTS：免金鑰，僅供開發測試（非官方 API，正式頻道請改用 Azure）。"""

    name = "edge"

    def __init__(self):
        self.voice = get_settings().voice_name

    @with_retry()
    def synthesize(self, text: str, out: Path, episode_id: int | None = None, rate: str = "-3%") -> VoiceResult:
        import edge_tts

        mp3 = out.with_suffix(".mp3")
        asyncio.run(edge_tts.Communicate(text, self.voice, rate=rate).save(str(mp3)))
        wav = out.with_suffix(".wav")
        # Edge 每段前後各帶約 0.2 / 0.9 秒靜音，段落接起來會變成一句一停；裁掉後由剪輯統一控制停頓
        run_ffmpeg(["-i", str(mp3), "-af", TRIM_SILENCE, "-ar", "48000", "-ac", "1", str(wav)])
        mp3.unlink(missing_ok=True)
        duration = media_duration(wav)
        # 偶爾會回傳被截斷的音檔（沒有報錯），依字數檢查長度，太短就重試
        if duration < len(text) / MAX_CHARS_PER_SECOND:
            raise RuntimeError(f"Edge TTS 音檔過短：{duration:.1f}s / {len(text)} 字")
        costs.record(episode_id, "edge", "tts", 0.0, chars=len(text))
        return VoiceResult(wav, duration, len(text))


class MockVoice:
    """產生與字數相符長度的靜音音軌，用於離線測試流程。"""

    name = "mock"

    def synthesize(self, text: str, out: Path, episode_id: int | None = None, rate: str = "-3%") -> VoiceResult:
        seconds = max(1.0, len(text) / (get_settings().narration_chars_per_minute / 60))
        wav = out.with_suffix(".wav")
        run_ffmpeg(["-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:duration={seconds:.2f}",
                    "-af", "volume=0.05", "-ac", "1", str(wav)])
        return VoiceResult(wav, media_duration(wav), len(text))
