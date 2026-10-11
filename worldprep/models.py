from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Episode(Base):
    __tablename__ = "episodes"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_number: Mapped[int] = mapped_column(Integer, unique=True)
    topic: Mapped[str] = mapped_column(String(300), default="")
    destination: Mapped[str] = mapped_column(String(120), default="")
    region: Mapped[str] = mapped_column(String(60), default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    script: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="IDEA")
    qa_status: Mapped[str] = mapped_column(String(20), default="")
    qa_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    contains_synthetic_media: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    drive_folder_url: Mapped[str] = mapped_column(String(300), default="")
    requested_topic: Mapped[str] = mapped_column(Text, default="")
    regenerate_from: Mapped[str | None] = mapped_column(String(40), nullable=True)
    failed_from: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sources: Mapped[list["ResearchSource"]] = relationship(back_populates="episode", cascade="all, delete-orphan")
    assets: Mapped[list["Asset"]] = relationship(back_populates="episode", cascade="all, delete-orphan")


class ResearchSource(Base):
    __tablename__ = "research_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"))
    claim: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(300), default="")
    source_url: Mapped[str] = mapped_column(String(1000), default="")
    source_type: Mapped[str] = mapped_column(String(60), default="")
    source_date: Mapped[str] = mapped_column(String(40), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    verdict: Mapped[str] = mapped_column(String(20), default="unchecked")
    episode: Mapped[Episode] = relationship(back_populates="sources")


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"))
    scene_id: Mapped[str] = mapped_column(String(20), default="")
    asset_type: Mapped[str] = mapped_column(String(30))
    source: Mapped[str] = mapped_column(String(300), default="")
    creator: Mapped[str] = mapped_column(String(300), default="")
    license: Mapped[str] = mapped_column(String(120), default="")
    license_url: Mapped[str] = mapped_column(String(1000), default="")
    usage_rights: Mapped[str] = mapped_column(String(300), default="")
    attribution_required: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    realistic: Mapped[bool] = mapped_column(Boolean, default=False)
    file_path: Mapped[str] = mapped_column(String(1000), default="")
    date_acquired: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    episode: Mapped[Episode] = relationship(back_populates="assets")


class ProductionJob(Base):
    __tablename__ = "production_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"))
    job_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="running")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")


class Cost(Base):
    __tablename__ = "costs"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int | None] = mapped_column(ForeignKey("episodes.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String(60))
    service: Mapped[str] = mapped_column(String(60))
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)
    actual_cost: Mapped[float] = mapped_column(Float, default=0.0)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StateTransition(Base):
    __tablename__ = "state_transitions"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"))
    from_state: Mapped[str] = mapped_column(String(40))
    to_state: Mapped[str] = mapped_column(String(40))
    note: Mapped[str] = mapped_column(Text, default="")
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int | None] = mapped_column(ForeignKey("episodes.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(60))
    actor: Mapped[str] = mapped_column(String(200), default="system")
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Topic(Base):
    __tablename__ = "topics"
    id: Mapped[int] = mapped_column(primary_key=True)
    destination: Mapped[str] = mapped_column(String(120), unique=True)
    region: Mapped[str] = mapped_column(String(60))
    angle: Mapped[str] = mapped_column(Text, default="")
    used_episode_id: Mapped[int | None] = mapped_column(ForeignKey("episodes.id"), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TopicOption(Base):
    """頻道主回信告知「後天的城市」；企劃為它想 3 個主題選項，放進下一封通知信的「主題選擇」區塊。"""
    __tablename__ = "topic_options"
    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(String(500), default="")
    city: Mapped[str] = mapped_column(String(120))
    options: Mapped[list | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    shown_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TopicRequest(Base):
    """頻道主回覆通知信指定的主題；依收到順序排隊，每次製作取最早一筆。"""
    __tablename__ = "topic_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(String(500), unique=True)
    text: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_episode_id: Mapped[int | None] = mapped_column(ForeignKey("episodes.id"), nullable=True)
