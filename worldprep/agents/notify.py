import html
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..brand import CHANNEL_NAME, SLOGAN, ep_label
from ..config import get_settings
from ..db import audit, session, transition
from ..logging_setup import log
from ..models import AuditLog, Episode
from ..states import State
from ..storage import get_storage
from .english import email as english_email

REQUEST_HINT = "想指定下一集主題？直接回覆這封信，寫下目的地或想看的角度（例如「京都：為什麼能活過千年」）。凌晨 2:00 前回覆，隔天 08:00 收到成品；沒有回覆的那天不製作。可一次回覆多封，系統每天做一集、依序製作。"
REQUEST_HINT_HTML = (f'<div style="background:#0b1b3a;color:#fff;border-radius:6px;padding:12px 16px;margin-top:20px">'
                     f'<b style="color:#d4a853">指定下一集</b><br>{html.escape(REQUEST_HINT)}</div>')
BOX = "background:#f5f3ee;border:1px solid #e0dccf;border-radius:6px;padding:14px;white-space:pre-wrap;font-size:14px;line-height:1.6"


def _section(label: str, body: str) -> str:
    return f'<h3 style="margin:22px 0 6px;font-size:15px">{label}</h3><div style="{BOX}">{html.escape(body)}</div>'


def episode_email(eid: int) -> tuple[str, str, str]:
    st = get_storage()
    meta = st.read_json(eid, "final", "metadata.json")
    with session() as s:
        ep = s.get(Episode, eid)
        n, dest, qa, synthetic, folder = ep.episode_number, ep.destination, ep.qa_status, ep.contains_synthetic_media, ep.drive_folder_url
        review = ep.needs_human_review
        warnings = [c for c in (ep.qa_report or {}).get("checks", []) if not c["pass"]]
        minutes = (ep.duration_seconds or 0) / 60
        requested = ep.requested_topic
    tags = ", ".join(meta["tags"])
    alternates = "\n".join(meta.get("title_alternates", []))
    checklist = [
        "上傳 episode.mp4",
        "貼上標題與說明（下方）；也可改用備選標題",
        "縮圖：thumbnail.jpg（thumbnail_candidates 內有其他候選）",
        "字幕：上傳 zh-Hant.srt（影片已燒錄中文字幕，此檔供 YouTube CC 與搜尋使用）",
        f"標籤：{tags}",
        "類別：旅遊與活動；觀眾：不是為兒童打造",
        "變造或合成內容：" + ("選「是」（本集含寫實的 AI 生成重建畫面）" if synthetic else "選「否」"),
        "短影音：正片發布後，上傳 short.mp4 當 Shorts（標題與說明見下方宣傳文案）；"
        "在 Shorts 的「相關影片」選本集正片，觀眾才點得到完整影片；封面用手機 YouTube App 上傳時選「上傳縮圖」放 short_cover.jpg",
        "宣傳：IG 用 thumbnail.jpg 發文、Threads 附上 short.mp4；文案中的【YouTube 正片連結】換成正片網址",
    ]
    social = meta.get("social") or {}
    promos = [("YouTube Shorts 標題", social.get("shorts_title", "")), ("YouTube Shorts 說明", social.get("shorts_description", "")),
              ("Instagram 貼文（配 thumbnail.jpg）", social.get("instagram_caption", "")),
              ("Threads 貼文（配 short.mp4）", social.get("threads_post", ""))]
    promos = [(label, body) for label, body in promos if body]
    warn_html = ""
    if review or warnings:
        warn_html = ('<div style="background:#fff4e0;border-left:4px solid #d4a853;padding:10px 14px;margin-top:16px">'
                     "<b>請特別檢查</b><br>" + "<br>".join(html.escape(f"{c['check']}：{c['detail']}") for c in warnings[:10]) + "</div>")
    folder_html = f'<p><a href="{html.escape(folder)}">開啟 Google Drive 資料夾</a></p>' if folder else ""
    body = f"""<div style="font-family:'Noto Sans TC','Microsoft JhengHei',sans-serif;max-width:680px;margin:auto;color:#0b1b3a">
<div style="background:#0b1b3a;color:#fff;padding:18px 22px"><b style="font-size:19px">{CHANNEL_NAME} {ep_label(n)} 已完成</b><br>
<span style="color:#d4a853">{SLOGAN}</span></div>
<div style="padding:18px 22px">
<p style="color:#556;margin-top:0">目的地：{html.escape(dest)}　｜　片長：約 {minutes:.0f} 分鐘　｜　QA：<b>{html.escape(qa)}</b></p>
<img src="cid:thumb" width="636" style="width:100%;border-radius:6px" alt="thumbnail">
{folder_html}{warn_html}
{f'<p style="color:#556">本集主題來自你的指定：「{html.escape(requested)}」</p>' if requested else ""}
{_section("YouTube 標題", meta["title"])}
{_section("備選標題", alternates) if alternates else ""}
{_section("YouTube 說明", meta["description"])}
{_section("標籤", tags)}
{_section("置頂留言（建議）", meta.get("pinned_comment", ""))}
{'<h2 style="margin:28px 0 0;font-size:17px;border-top:2px solid #d4a853;padding-top:14px">宣傳文案（導流到 YouTube 正片）</h2>' if promos else ""}
{"".join(_section(label, body) for label, body in promos)}
<h3 style="margin:22px 0 6px;font-size:15px">上傳檢查清單</h3>
<ol style="line-height:1.8;padding-left:20px">{"".join(f"<li>{html.escape(x)}</li>" for x in checklist)}</ol>
{REQUEST_HINT_HTML}
<p style="color:#889;font-size:12px">不滿意要重做：GitHub → Actions → regenerate → episode_id 填 <b>{eid}</b>，選擇要重做的部分（標題 / 縮圖 / 腳本 / 指定場景 / 整支影片）。</p>
</div></div>"""
    text = (f"{CHANNEL_NAME} {ep_label(n)} 已完成\n\nDrive：{folder}\nQA：{qa}\n\n【YouTube 標題】\n{meta['title']}\n\n"
            + (f"【備選標題】\n{alternates}\n\n" if alternates else "")
            + f"【YouTube 說明】\n{meta['description']}\n\n【標籤】\n{tags}\n\n【置頂留言】\n{meta.get('pinned_comment', '')}\n\n"
            + "".join(f"【{label}】\n{body}\n\n" for label, body in promos)
            + "【上傳檢查清單】\n" + "\n".join(f"- {x}" for x in checklist)
            + f"\n\n{REQUEST_HINT}\n重做：GitHub Actions → regenerate，episode_id = {eid}")
    subject = f"{CHANNEL_NAME} {ep_label(n)} 已完成：{meta['main_title']}"
    return subject, body, text


def send_daily(p) -> list[int]:
    """08:00：寄出所有已交付、尚未通知的集數；沒有新集數時寄狀態信，讓你知道系統還活著。"""
    cfg = get_settings()
    st = get_storage()
    with session() as s:
        ids = list(s.scalars(select(Episode.id).where(Episode.status == State.DELIVERED.value).order_by(Episode.id)))
    for eid in ids:
        subject, body, text = episode_email(eid)
        p.email.send(cfg.email_to or "owner@localhost", subject, body, text,
                     {"thumb": st.path(eid, "thumbnails", "thumbnail.jpg")})
        en = english_email(eid) if cfg.english_enabled else None  # 英文頻道 Beyond Travel 另寄一封
        if en:
            p.email.send(cfg.email_to or "owner@localhost", en[0], en[1], en[2], {"thumb": en[3]})
        with session() as s:
            ep = s.get(Episode, eid)
            transition(s, ep, State.NOTIFIED, "daily email sent")
            ep.notified_at = datetime.now(timezone.utc)
            audit(s, "daily_email_sent", eid, to=cfg.email_to)
        log.info("daily_email_sent", extra={"episode_id": eid})
    # 排程一天會觸發好幾次（GitHub 排程常延遲或漏跑）：當天寄過任何一封信，就不再寄「今日沒有新影片」
    tz = ZoneInfo(cfg.timezone)
    day_start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
    with session() as s:
        sent_today = s.scalar(select(AuditLog.id).where(AuditLog.action.in_(["daily_email_sent", "daily_status_sent"]),
                                                        AuditLog.timestamp >= day_start).limit(1))
    if not ids and sent_today is None:
        with session() as s:
            audit(s, "daily_status_sent")
        with session() as s:
            ep = s.scalar(select(Episode).order_by(Episode.id.desc()))
            status = f"{ep_label(ep.episode_number)} {ep.destination}：{ep.status}" if ep else "尚無集數"
            err = ep.last_error if ep else ""
        p.email.send(cfg.email_to or "owner@localhost", f"{CHANNEL_NAME} 今日沒有新影片",
                     f"<p>目前狀態：{html.escape(status)}</p>" + (f"<pre>{html.escape(err[:3000])}</pre>" if err else "")
                     + REQUEST_HINT_HTML,
                     f"目前狀態：{status}\n{err[:3000]}\n\n{REQUEST_HINT}")
    return ids
