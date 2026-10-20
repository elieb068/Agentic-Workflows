"""Public archive of the weekly newsletter (/newsletter).

The n8n workflow publishes each approved edition with PUT /api/newsletter/issues/<edition> (Bearer
NEWSLETTER_STATS_TOKEN). An edition shows up only from its publish_at on (the send time), so subscribers get it
first. The text is the structured draft Claude wrote and the editor approved; edits made only in Brevo before
approving are not reflected here.
"""

from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from raioxia.db import NewsletterIssue

Text = Annotated[str, Field(min_length=1, max_length=4000)]
Short = Annotated[str, Field(min_length=1, max_length=300)]


def _http_url(value: str) -> str:
    if not value.startswith(("https://", "http://")):
        raise ValueError("links must be http(s) URLs")
    return value


Url = Annotated[str, Field(max_length=2048), AfterValidator(_http_url)]


class MainStory(BaseModel):
    title: Short
    paragraphs: list[Text] = Field(min_length=1, max_length=8)
    source_name: Short
    source_url: Url


class NumberOfWeek(BaseModel):
    value: Short
    label: Short
    explanation: Text


class Tip(BaseModel):
    title: Short
    body: Text
    guide_title: Short
    guide_url: Url


class OtherNews(BaseModel):
    title: Short
    summary: Text
    source_url: Url


class IssueContent(BaseModel):
    intro: Text
    main_story: MainStory
    number_of_week: NumberOfWeek | None = None
    tip: Tip
    other_news: list[OtherNews] = Field(default_factory=list, max_length=5)


class IssueIn(BaseModel):
    subject: Annotated[str, Field(min_length=1, max_length=200)]
    preheader: Annotated[str, Field(max_length=300)] = ""
    publish_at: datetime
    content: IssueContent


def upsert(session: Session, edition: int, issue: IssueIn) -> NewsletterIssue:
    row = session.get(NewsletterIssue, edition)
    if row is None:
        row = NewsletterIssue(edition=edition)
        session.add(row)
    row.subject = issue.subject
    row.preheader = issue.preheader
    row.publish_at = issue.publish_at
    row.content = issue.content.model_dump(mode="json")
    session.flush()
    return row


def published(session: Session, now: datetime) -> list[NewsletterIssue]:
    return list(session.scalars(
        select(NewsletterIssue).where(NewsletterIssue.publish_at <= now).order_by(NewsletterIssue.edition.desc())
    ))


def get_published(session: Session, edition: int, now: datetime) -> NewsletterIssue | None:
    row = session.get(NewsletterIssue, edition)
    return row if row is not None and row.publish_at <= now else None
