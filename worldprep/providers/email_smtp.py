import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

from ..config import get_settings
from ..retry import PermanentError, with_retry


class SMTPEmail:
    def __init__(self):
        s = get_settings()
        if not (s.smtp_user and s.smtp_password and s.email_to):
            raise PermanentError("SMTP_USER / SMTP_PASSWORD / EMAIL_TO 未設定")
        self.s = s

    @with_retry()
    def send(self, to: str, subject: str, html: str, text: str, inline_images: dict[str, Path] | None = None) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.s.email_from or self.s.smtp_user
        msg["To"] = to
        msg["Message-ID"] = make_msgid(domain="worldprep")
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        html_part = msg.get_payload()[1]
        for cid, path in (inline_images or {}).items():
            html_part.add_related(Path(path).read_bytes(), "image", "jpeg", cid=f"<{cid}>")
        ctx = ssl.create_default_context()
        with smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=60) as smtp:
            smtp.starttls(context=ctx)
            try:
                smtp.login(self.s.smtp_user, self.s.smtp_password)
            except smtplib.SMTPAuthenticationError as e:
                raise PermanentError(f"SMTP 登入失敗: {e}") from e
            smtp.send_message(msg)


class OutboxEmail:
    """未設定 SMTP 時寫入 storage/outbox，方便本地檢視。"""

    def __init__(self):
        self.dir = get_settings().storage_root / "outbox"
        self.dir.mkdir(parents=True, exist_ok=True)

    def send(self, to: str, subject: str, html: str, text: str, inline_images: dict[str, Path] | None = None) -> None:
        import re
        import time

        for cid, path in (inline_images or {}).items():
            html = html.replace(f"cid:{cid}", Path(path).resolve().as_uri())
        name = re.sub(r"[^\w]+", "_", subject)[:60]
        (self.dir / f"{int(time.time())}_{name}.html").write_text(html, encoding="utf-8")
