import os

import typer

app = typer.Typer(add_completion=False, help="世界先修課 自動化生產系統")


class _RunLock:
    """避免排程與手動執行同時處理同一集（超過 8 小時的鎖視為殘留）。"""

    def __init__(self, name: str = "produce"):
        from pathlib import Path

        from .config import ROOT

        self.path = Path(ROOT) / "data" / f"{name}.lock"

    def __enter__(self):
        import time

        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and time.time() - self.path.stat().st_mtime < 8 * 3600:
            typer.echo(f"另一個製作程序正在執行（{self.path}），本次略過")
            raise typer.Exit(0)
        self.path.write_text(str(os.getpid()), encoding="utf-8")
        return self

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def _setup(mock: bool):
    if mock:
        os.environ["MOCK"] = "true"
        os.environ.setdefault("DATABASE_URL", "sqlite:///./data/mock.db")
        os.environ.setdefault("STORAGE_ROOT", "./storage_mock")
        os.environ.setdefault("TARGET_VIDEO_LENGTH_MINUTES", "1")
    from .db import init_db
    from .providers import get_providers

    init_db()
    return get_providers()


@app.command()
def init():
    """建立資料庫、匯入選題池、產生 logo。"""
    _setup(False)
    from .agents.topic import seed_topics
    from .render.logo import generate_all

    seed_topics()
    generate_all()
    typer.echo("ok")


@app.command()
def brand():
    """產生 logo 全套檔案到 brand/。"""
    from .render.logo import generate_all

    for p in generate_all():
        typer.echo(p)


@app.command()
def run(destination: str = typer.Option(None, help="指定目的地，省略則由 Topic Agent 選題"),
        mock: bool = typer.Option(False, help="離線 mock 模式"),
        email: bool = typer.Option(True, help="完成後立即寄出通知信")):
    """完整製作一集：選題 → … → QA → 交付 Google Drive →（通知信）。"""
    with _RunLock():
        p = _setup(mock)
        from .pipeline import produce, send_daily_email

        eid = produce(p, destination)
        if email:
            send_daily_email(p)
        if eid:
            _show(eid)


@app.command()
def resume(episode_id: int, mock: bool = False):
    """從最後成功的階段續跑。"""
    with _RunLock():
        p = _setup(mock)
        from .pipeline import advance

        advance(p, episode_id)
        _show(episode_id)


@app.command()
def regenerate(episode_id: int, target: str = typer.Option(..., help="title | thumbnail | script | scenes | video"),
               feedback: str = "", mock: bool = False):
    """看完不滿意時重做指定部分，完成後重新交付 Drive，下一封 08:00 信會通知。"""
    with _RunLock():
        p = _setup(mock)
        from .pipeline import advance, regenerate as regen

        regen(p, episode_id, target, feedback)
        advance(p, episode_id)
        _show(episode_id)


@app.command("send-email")
def send_email_cmd(mock: bool = False):
    """寄出所有已交付、尚未通知集數的通知信。"""
    p = _setup(mock)
    from .pipeline import send_daily_email

    typer.echo(send_daily_email(p))


@app.command()
def status(mock: bool = False):
    _setup(mock)
    from sqlalchemy import select

    from .db import session
    from .models import Episode

    with session() as s:
        for e in s.scalars(select(Episode).order_by(Episode.episode_number)):
            typer.echo(f"EP.{e.episode_number:02d} id={e.id} {e.destination} {e.status} qa={e.qa_status} {e.title} {e.drive_folder_url}")


@app.command("google-auth")
def google_auth():
    """一次性 OAuth 授權 Google Drive（開啟瀏覽器）。"""
    from .drive import authorize_interactive

    typer.echo(authorize_interactive())


@app.command()
def schedule():
    """本機常駐排程（Asia/Taipei）；主要部署方式為 GitHub Actions。"""
    from .scheduler import main

    main()


@app.command("ci-pull")
def ci_pull():
    """GitHub Actions 開工：從 Drive 取回資料庫、配樂與工作檔。"""
    # 必須在資料庫連線建立前下載 DB，避免覆蓋開著的 SQLite 檔
    from .drive import pull_state

    pull_state()


@app.command("ci-push")
def ci_push():
    """GitHub Actions 收工：狀態寫回 Drive（失敗時也要執行）。"""
    _setup(False)
    from .drive import push_state

    push_state()


@app.command("ci-produce")
def ci_produce(destination: str = typer.Option(None)):
    """每日製作：續跑或選題 → QA → 交付 Drive。"""
    with _RunLock():
        p = _setup(False)
        from .pipeline import produce

        eid = produce(p, destination or None)
        if eid:
            _show(eid)
        else:
            typer.echo("沒有新的指定主題，或今晚已製作過一集，本次不製作")


@app.command("ci-regenerate")
def ci_regenerate(episode_id: int, target: str, feedback: str = ""):
    with _RunLock():
        p = _setup(False)
        from .pipeline import advance, regenerate as regen

        regen(p, episode_id, target, feedback)
        advance(p, episode_id)
        _show(episode_id)


@app.command("ci-email")
def ci_email():
    """08:00 通知信：標題、YouTube 說明、標籤、Drive 連結、上傳檢查清單。"""
    p = _setup(False)
    from .pipeline import send_daily_email

    typer.echo(send_daily_email(p))


@app.command("upload-music")
def upload_music_cmd(path: str):
    """把有授權的配樂上傳到 Drive（公開 repo 不放音樂檔），並記得登記到 music/library.json。"""
    _setup(False)
    from pathlib import Path

    from .drive import upload_music

    upload_music(Path(path))


def _show(eid: int) -> None:
    from .db import session
    from .models import Episode

    with session() as s:
        e = s.get(Episode, eid)
        typer.echo(f"EP.{e.episode_number:02d} {e.destination} → {e.status} | QA {e.qa_status} | {e.title}")
        typer.echo(f"drive: {e.drive_folder_url}")


if __name__ == "__main__":
    app()
