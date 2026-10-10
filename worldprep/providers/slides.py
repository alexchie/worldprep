import base64
import time
from pathlib import Path

from .. import costs
from ..config import get_settings
from ..logging_setup import log
from ..retry import PermanentError, with_retry
from .base import ImageResult

DONE_STATES = {"JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"}
IMAGE_CONFIG = {"aspect_ratio": "16:9", "image_size": "1K"}  # 投影片用 1K（比 2K 便宜三分之一）
COVER_IMAGE_CONFIG = {"aspect_ratio": "16:9", "image_size": "2K"}  # 封面字多，維持 2K（Nano Banana Pro 1K、2K 同價）
MIN_BATCH = 10  # 少量重做直接即時生成，不值得排隊


class GeminiSlides:
    """Gemini 生圖（Nano Banana）：整集投影片先送 Batch（半價），等太久或失敗的再逐張即時生成。"""

    name = "gemini"

    def __init__(self):
        from google import genai

        s = get_settings()
        if not s.gemini_api_key:
            raise PermanentError("GEMINI_API_KEY 未設定")
        self.client = genai.Client(api_key=s.gemini_api_key)
        self.model, self.price = s.slide_model, s.slide_price_usd
        # Google 依 token 計費（含思考與參考圖），實際帳單約比每張定價多 25–30%：記帳時保守估計
        self.factor = s.gemini_cost_factor
        self.batch, self.batch_wait = s.batch_enabled, s.slide_batch_wait_minutes * 60

    def _result(self, path: Path) -> ImageResult:
        return ImageResult(path=path, source=f"gemini:{self.model}", creator="AI generated（Google Gemini）",
                           license="Generated", license_url="https://ai.google.dev/gemini-api/terms",
                           usage_rights="owned output", ai_generated=True, realistic=True)

    def _batched(self, jobs: dict[str, tuple[str, Path]], episode_id: int) -> dict[str, ImageResult]:
        keys = list(jobs)
        src = [{"contents": [{"parts": [{"text": jobs[k][0]}]}],
                "config": {"response_modalities": ["IMAGE"], "image_config": IMAGE_CONFIG}} for k in keys]
        job = self.client.batches.create(model=self.model, src=src, config={"display_name": f"worldprep-ep{episode_id}"})
        deadline = time.monotonic() + self.batch_wait
        while True:
            job = self.client.batches.get(name=job.name)
            if job.state.name in DONE_STATES:
                break
            if time.monotonic() > deadline:
                self.client.batches.cancel(name=job.name)
                log.warning("slide_batch_timeout", extra={"episode_id": episode_id, "batch": job.name})
                return {}
            time.sleep(30)
        out: dict[str, ImageResult] = {}
        if job.state.name == "JOB_STATE_SUCCEEDED":
            for k, r in zip(keys, job.dest.inlined_responses):
                data = next((part.inline_data.data for c in (r.response.candidates if r.response else [])
                             for part in c.content.parts if part.inline_data), None)
                if data:
                    path = jobs[k][1].with_suffix(".jpg")
                    path.write_bytes(data if isinstance(data, bytes) else base64.b64decode(data))
                    out[k] = self._result(path)
        costs.record(episode_id, "gemini", "slides_batch", len(out) * self.price * 0.5 * self.factor, images=len(out), model=self.model)
        log.info("slide_batch_done", extra={"episode_id": episode_id, "state": job.state.name, "images": len(out), "requested": len(keys)})
        return out

    @with_retry(attempts=3)
    def left(self, episode_id: int | None, reserve: float = 0.0) -> float:
        """本集 Gemini 還能花多少（扣掉保留額度）。"""
        if episode_id is None:
            return float("inf")
        return get_settings().gemini_episode_budget_usd - costs.provider_total(episode_id, "gemini") - reserve

    def _guard(self, episode_id: int | None, price: float, reserve: float = 0.0) -> None:
        if price * self.factor > self.left(episode_id, reserve):
            raise costs.BudgetExceeded(f"本集 Gemini 已接近上限 {get_settings().gemini_episode_budget_usd} USD")

    def get(self, prompt: str, out: Path, episode_id: int | None = None, reserve: float = 0.0) -> ImageResult | None:
        self._guard(episode_id, self.price, reserve)
        r = self.client.interactions.create(model=self.model, input=prompt,
                                            response_format={"type": "image", "mime_type": "image/jpeg", **IMAGE_CONFIG})
        path = out.with_suffix(".jpg")
        path.write_bytes(base64.b64decode(r.output_image.data))
        costs.record(episode_id, "gemini", "slide", self.price * self.factor, model=self.model)
        return self._result(path)

    @with_retry(attempts=3)
    def compose(self, prompt: str, references: list[Path], out: Path, episode_id: int | None = None,
                model: str | None = None, price: float | None = None, reserve: float = 0.0) -> ImageResult:
        """帶參考圖生成單張圖：封面用較強的模型、參考圖當系列設計規範；也用來把投影片上的中文字換成英文。"""
        from google.genai import types

        s = get_settings()
        model, price = model or s.cover_model, s.cover_price_usd if price is None else price
        self._guard(episode_id, price, reserve)
        parts = [types.Part.from_bytes(data=ref.read_bytes(), mime_type="image/png" if ref.suffix.lower() == ".png" else "image/jpeg")
                 for ref in references]
        r = self.client.models.generate_content(
            model=model, contents=[*parts, prompt],
            config=types.GenerateContentConfig(response_modalities=["IMAGE"], image_config=types.ImageConfig(
                **(COVER_IMAGE_CONFIG if model == s.cover_model else IMAGE_CONFIG))),
        )
        data = next((part.inline_data.data for c in r.candidates or [] for part in c.content.parts if part.inline_data), None)
        if not data:
            raise RuntimeError("生圖沒有回傳圖片")
        path = out.with_suffix(".png")
        path.write_bytes(data if isinstance(data, bytes) else base64.b64decode(data))
        costs.record(episode_id, "gemini", "compose", price * self.factor, model=model)
        return self._result(path)

    def generate_many(self, jobs: dict[str, tuple[str, Path]], episode_id: int) -> dict[str, ImageResult]:
        """jobs: {scene_id: (prompt, out_path)}；超出 Gemini 上限（扣掉封面保留額度）的場景不生成，交給呼叫端改用免費圖庫。"""
        if not jobs:
            return {}
        done: dict[str, ImageResult] = {}
        reserve = get_settings().gemini_cover_reserve_usd
        affordable = int(max(0.0, self.left(episode_id, reserve)) // (self.price * 0.5 * self.factor)) if episode_id is not None else len(jobs)
        if affordable < len(jobs):
            log.warning("slides_over_budget", extra={"episode_id": episode_id, "requested": len(jobs), "affordable": affordable})
            jobs = dict(list(jobs.items())[:affordable])
        if self.batch and len(jobs) >= MIN_BATCH:
            try:
                done = self._batched(jobs, episode_id)
            except Exception as e:
                log.warning("slide_batch_failed", extra={"episode_id": episode_id, "err": str(e)[:300]})
        for k, (prompt, out) in jobs.items():
            if k in done:
                continue
            if self.price * self.factor > self.left(episode_id, reserve) or not costs.check_budget(episode_id, self.price):
                break
            try:
                r = self.get(prompt, out, episode_id, reserve)
            except Exception as e:
                log.warning("slide_failed", extra={"episode_id": episode_id, "scene": k, "err": str(e)[:300]})
                continue
            if r:
                done[k] = r
        return done
