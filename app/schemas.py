from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import MAX_RECIPIENTS


class RecipientIn(BaseModel):
    """A recipient. Fields default to "" so that a *bad recipient* is reported
    per-recipient (FAILED) instead of rejecting the whole request."""

    name: str = ""
    email: str = ""


class JobCreate(BaseModel):
    event_name: str = Field(max_length=200)
    issued_date: date | None = None  # defaults to today
    recipients: list[RecipientIn] = Field(min_length=1, max_length=MAX_RECIPIENTS)

    @field_validator("event_name")
    @classmethod
    def _event_name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("event_name must not be blank")
        return v


class CertificateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    recipient_name: str
    recipient_email: str
    status: str
    error_message: str | None = None
    download_url: str | None = None


class JobSummary(BaseModel):
    total: int
    succeeded: int
    failed: int
    pending: int


class JobCreated(BaseModel):
    job_id: str
    status: str
    total: int
    status_url: str


class JobOut(BaseModel):
    job_id: str
    event_name: str
    issued_date: date
    status: str
    created_at: datetime
    completed_at: datetime | None
    summary: JobSummary
    download_all_url: str | None
    certificates: list[CertificateOut]
