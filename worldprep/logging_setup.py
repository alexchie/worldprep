import json
import logging
import sys
from datetime import datetime, timezone

from .config import get_settings

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        data.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False, default=str)


def _build() -> logging.Logger:
    logger = logging.getLogger("worldprep")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    d = get_settings().log_dir
    d.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(d / "pipeline.jsonl", encoding="utf-8")
    fh.setFormatter(JsonFormatter())
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


log = _build()
