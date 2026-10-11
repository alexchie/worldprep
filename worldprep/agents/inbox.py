"""讀取頻道主回覆通知信所指定的主題（IMAP，沿用寄信帳號的 App Password）。"""
import email
import imaplib
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

from sqlalchemy import select

from ..brand import CHANNEL_NAME
from ..config import get_settings
from ..db import session
from ..logging_setup import log
from ..models import TopicOption, TopicRequest
from ..retry import with_retry

LOOKBACK_DAYS = 14
QUOTE_MARKERS = [
    re.compile(r"^>"),
    re.compile(r".*(wrote|寫道|写道)[:：]?\s*$"),
    re.compile(r"^-{2,}\s*(Original Message|原始郵件)?"),
    re.compile(r"^(From|寄件者)[:：]"),
]


def authenticated_sender(msg: email.message.Message, expected: str) -> bool:
    """只接受 From 為指定信箱、且 Gmail 判定 DKIM 或 DMARC 通過的信，避免他人冒名指定主題。"""
    sender = parseaddr(msg.get("From", ""))[1].lower()
    if sender != expected.lower():
        return False
    results = " ".join(msg.get_all("Authentication-Results", [])).lower()
    domain = expected.split("@")[-1].lower()
    return "dmarc=pass" in results or bool(re.search(rf"dkim=pass[^;]*header\.(i|d)=@?{re.escape(domain)}", results))


def _plain_body(msg: email.message.Message) -> str:
    part = msg
    if msg.is_multipart():
        part = next((p for p in msg.walk() if p.get_content_type() == "text/plain" and not p.get_filename()), None)
        if part is None:
            html_part = next((p for p in msg.walk() if p.get_content_type() == "text/html"), None)
            if html_part is None:
                return ""
            raw = html_part.get_payload(decode=True).decode(html_part.get_content_charset() or "utf-8", "replace")
            raw = re.sub(r"<blockquote.*", "", raw, flags=re.S | re.I)
            return re.sub(r"<[^>]+>", "\n", raw)
    return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")


def extract_topic(body: str) -> str:
    """取回覆中你新寫的文字（引用原信之前的部分）。"""
    lines = []
    for line in body.replace("\r", "").split("\n"):
        s = line.strip()
        if any(p.match(s) for p in QUOTE_MARKERS):
            break
        if s:
            lines.append(s)
    return " ".join(lines)[:500]


def shown_options() -> list[dict] | None:
    """最近一封通知信提供的主題選項（回信寫「選 2」時用來對照）。"""
    with session() as s:
        row = s.scalar(select(TopicOption).where(TopicOption.shown_at.is_not(None)).order_by(TopicOption.shown_at.desc()))
        return [dict(o, city=row.city) for o in row.options] if row and row.options else None


def resolve(p, text: str) -> tuple[str, str]:
    """回信 → (明天的主題, 後天的城市)。沒有 LLM 時整封當作主題（舊的回信方式）。"""
    if p is None:
        return text, ""
    options = shown_options()
    from .topic import parse_reply

    r = parse_reply(p, text, options)
    topic = r["custom_topic"].strip()
    if topic and r["custom_is_full_title"]:
        topic = f"{topic}\n指定標題：{topic}"  # 頻道主寫的是完整標題：照原句使用
    if not topic and options and 1 <= r["option_number"] <= len(options):
        o = options[r["option_number"] - 1]
        topic = f"{o['city']}：{o['angle']}\n指定標題：{o['main_title']}"
    return topic, r["next_city"].strip()


@with_retry(attempts=3)
def fetch_requests(p=None) -> int:
    """抓取新的回信：明天的主題存入排隊，後天的城市交給企劃想選項。回傳新增筆數。"""
    cfg = get_settings()
    if not (cfg.smtp_user and cfg.smtp_password and cfg.email_to):
        return 0
    since = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%d-%b-%Y")
    added = 0
    with imaplib.IMAP4_SSL(cfg.imap_host) as imap:
        imap.login(cfg.smtp_user, cfg.smtp_password.replace(" ", ""))
        imap.select("INBOX", readonly=True)
        _, data = imap.search(None, "FROM", f'"{cfg.email_to}"', "SINCE", since)
        with session() as s:
            known = set(s.scalars(select(TopicRequest.message_id))) | set(s.scalars(select(TopicOption.message_id)))
        for num in data[0].split():
            _, parts = imap.fetch(num, "(BODY.PEEK[])")
            msg = email.message_from_bytes(parts[0][1])
            mid = msg.get("Message-ID", "").strip() or f"num-{num.decode()}"
            if mid in known:
                continue
            subject = str(make_header(decode_header(msg.get("Subject", ""))))
            if CHANNEL_NAME not in subject:
                continue
            if not authenticated_sender(msg, cfg.email_to):
                log.warning("topic_email_rejected_unauthenticated", extra={"from": msg.get("From", "")[:120]})
                continue
            text = extract_topic(_plain_body(msg))
            if not text:
                continue
            try:
                received = parsedate_to_datetime(msg.get("Date"))
            except Exception:
                received = datetime.now(timezone.utc)
            topic, city = resolve(p, text)
            with session() as s:
                if topic:
                    s.add(TopicRequest(message_id=mid[:500], text=topic, received_at=received))
                if city:
                    s.add(TopicOption(message_id=mid[:500], city=city[:120]))
            known.add(mid)
            added += 1
            log.info("topic_request_received", extra={"subject": subject[:100], "topic": topic[:100], "next_city": city})
    return added


def next_request() -> TopicRequest | None:
    """每天只做一集：以最新一封回信的主題為準（較舊、沒用到的回信在建立集數時一併作廢）。"""
    with session() as s:
        return s.scalar(select(TopicRequest).where(TopicRequest.used_episode_id.is_(None))
                        .order_by(TopicRequest.received_at.desc()))


def pending_count() -> int:
    with session() as s:
        return len(list(s.scalars(select(TopicRequest.id).where(TopicRequest.used_episode_id.is_(None)))))
