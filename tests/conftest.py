import os
import sys
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="worldprep_test_"))
os.environ.update({
    "MOCK": "true",
    "DATABASE_URL": f"sqlite:///{(_tmp / 'test.db').as_posix()}",
    "STORAGE_ROOT": str(_tmp / "storage"),
    "LOG_DIR": str(_tmp / "logs"),
    "TARGET_VIDEO_LENGTH_MINUTES": "1",
    "SMTP_USER": "",
    "MOTION": "off",
})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
