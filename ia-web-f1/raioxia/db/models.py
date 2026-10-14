"""Database schema (PostgreSQL). Migrations live in raioxia/migrations (Alembic).

Personal data: leads.email, consents.ip/user_agent, analyses.requested_ip and
email_outbox.to_address. Deleting a lead cascades to its consents and outbox rows;
analyses survive with lead_id NULL (anonymized stats) unless deleted explicitly.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Stable constraint names, so Alembic migrations are predictable.
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

ANALYSIS_STATUSES = ("pending_confirmation", "queued", "running", "done", "failed", "expired")
CONSENT_KINDS = ("report_terms", "newsletter")
EMAIL_KINDS = ("confirm", "report", "failed", "deletion")
EMAIL_STATUSES = ("queued", "sent", "failed")
CHECK_STATUSES = ("ok", "aviso", "erro", "indeterminado")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = {datetime: DateTime(timezone=True), dict[str, Any]: JSONB}


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)  # normalized (lowercase)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    confirmed_at: Mapped[datetime | None]  # first confirmed e-mail
    newsletter: Mapped[bool] = mapped_column(server_default=text("false"))  # current state; history in consents
    unsubscribed_at: Mapped[datetime | None]  # no e-mail at all after this, except deletion confirmations

    consents: Mapped[list["Consent"]] = relationship(back_populates="lead", cascade="all, delete-orphan", passive_deletes=True)


class Consent(Base):
    """Append-only: a withdrawal is a new row with granted=false."""

    __tablename__ = "consents"
    __table_args__ = (CheckConstraint(_in("kind", CONSENT_KINDS), name="kind"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    granted: Mapped[bool]
    text_version: Mapped[str] = mapped_column(String(32))
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    lead: Mapped[Lead] = relationship(back_populates="consents")


class Analysis(Base):
    __tablename__ = "analyses"
    __table_args__ = (
        CheckConstraint(_in("status", ANALYSIS_STATUSES), name="status"),
        CheckConstraint("score IS NULL OR score BETWEEN 0 AND 100", name="score"),
        Index("ix_analyses_domain_finished_at", "domain", "finished_at"),  # 24h reuse lookup
        Index("ix_analyses_status_created_at", "status", "created_at"),  # expiry and retention
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), index=True)
    url: Mapped[str] = mapped_column(Text)  # normalized home URL
    domain: Mapped[str] = mapped_column(String(253))
    status: Mapped[str] = mapped_column(String(24), server_default="pending_confirmation")
    confirm_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)  # sha256 hex; NULL once used
    confirm_expires_at: Mapped[datetime | None]
    report_token: Mapped[str] = mapped_column(String(64), unique=True)
    requested_ip: Mapped[str | None] = mapped_column(INET)
    # Newsletter box ticked on this request; applied to the lead only when the e-mail is confirmed.
    newsletter_opt_in: Mapped[bool] = mapped_column(server_default=text("false"))
    reused_from_id: Mapped[int | None] = mapped_column(ForeignKey("analyses.id", ondelete="SET NULL"))
    score: Mapped[int | None] = mapped_column(SmallInteger)
    band_id: Mapped[str | None] = mapped_column(String(32))
    band_label: Mapped[str | None] = mapped_column(String(64))
    final_url: Mapped[str | None] = mapped_column(Text)
    version: Mapped[str | None] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    confirmed_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]

    lead: Mapped[Lead | None] = relationship()
    results: Mapped[list["CheckResultRow"]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan", passive_deletes=True, order_by="CheckResultRow.position"
    )


class CheckResultRow(Base):
    """One CheckResult (models.py); field names follow the public JSON contract."""

    __tablename__ = "check_results"
    __table_args__ = (CheckConstraint(_in("status", CHECK_STATUSES), name="status"),)

    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), primary_key=True)
    check_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    position: Mapped[int] = mapped_column(SmallInteger)  # order in the report
    status: Mapped[str] = mapped_column(String(16))
    titulo: Mapped[str] = mapped_column(Text)
    explicacao: Mapped[str] = mapped_column(Text)
    como_corrigir: Mapped[str] = mapped_column(Text, server_default="")
    evidencia: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    peso: Mapped[float] = mapped_column(Float)

    analysis: Mapped[Analysis] = relationship(back_populates="results")


class EmailOutbox(Base):
    """Every e-mail the app sends. Rendered at send time from template `kind` + `context`."""

    __tablename__ = "email_outbox"
    __table_args__ = (
        CheckConstraint(_in("kind", EMAIL_KINDS), name="kind"),
        CheckConstraint(_in("status", EMAIL_STATUSES), name="status"),
        Index("ix_email_outbox_status_created_at", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    analysis_id: Mapped[int | None] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    to_address: Mapped[str] = mapped_column(String(320))
    context: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(16), server_default="queued")
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    provider_message_id: Mapped[str | None] = mapped_column(String(128))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    sent_at: Mapped[datetime | None]
