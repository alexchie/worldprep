from tenacity import before_sleep_log, retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

import logging

from .logging_setup import log


class PermanentError(Exception):
    """不應重試的錯誤（設定錯誤、授權失敗、內容違規等）。"""


def with_retry(attempts: int = 4, initial: float = 2.0, max_wait: float = 60.0):
    return retry(
        reraise=True,
        stop=stop_after_attempt(attempts),
        wait=wait_exponential_jitter(initial=initial, max=max_wait),
        retry=retry_if_exception(lambda e: not isinstance(e, PermanentError)),
        before_sleep=before_sleep_log(log, logging.WARNING),
    )
