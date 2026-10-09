"""Business logic: creating jobs and processing them in the background."""
import logging
import re
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.certificate import generate_certificate
from app.config import OUTPUT_DIR
from app.database import SessionLocal
from app.models import Certificate, CertStatus, Job, JobStatus
from app.schemas import JobCreate, RecipientIn

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_NAME_LEN = 100


def validate_recipient(r: RecipientIn) -> str | None:
    """Return an error message if the recipient is invalid, else None."""
    name = r.name.strip()
    if not name:
        return "name is required"
    if len(name) > MAX_NAME_LEN:
        return f"name must be at most {MAX_NAME_LEN} characters"
    email = r.email.strip()
    if not email:
        return "email is required"
    if len(email) > 320 or not EMAIL_RE.match(email):
        return "email is not a valid address"
    return None


def create_job(db: Session, payload: JobCreate) -> Job:
    """Store the job and one Certificate row per recipient.

    Invalid recipients are stored immediately as FAILED (with the reason);
    valid ones are PENDING and will be picked up by process_job().
    """
    job = Job(
        event_name=payload.event_name,
        issued_date=payload.issued_date or date.today(),
        total=len(payload.recipients),
        status=JobStatus.PENDING,
    )
    for seq, r in enumerate(payload.recipients):
        error = validate_recipient(r)
        job.certificates.append(
            Certificate(
                seq=seq,
                recipient_name=r.name.strip(),
                recipient_email=r.email.strip(),
                status=CertStatus.FAILED if error else CertStatus.PENDING,
                error_message=error,
            )
        )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def count_by_status(db: Session, job_id: str) -> dict[str, int]:
    rows = db.execute(
        select(Certificate.status, func.count())
        .where(Certificate.job_id == job_id)
        .group_by(Certificate.status)
    ).all()
    counts = {CertStatus.PENDING: 0, CertStatus.SUCCESS: 0, CertStatus.FAILED: 0}
    counts.update({status: n for status, n in rows})
    return counts


def _finalize(db: Session, job: Job) -> None:
    counts = count_by_status(db, job.id)
    if counts[CertStatus.FAILED] == 0:
        job.status = JobStatus.COMPLETED
    elif counts[CertStatus.SUCCESS] == 0:
        job.status = JobStatus.FAILED
    else:
        job.status = JobStatus.COMPLETED_WITH_ERRORS
    job.completed_at = datetime.now(timezone.utc)
    db.commit()


def process_job(job_id: str) -> None:
    """Generate a PDF for every PENDING certificate of the job.

    Runs in a background task, so it opens its OWN database session.
    Each certificate is handled inside its own try/except: one failure is
    recorded on that certificate and the loop carries on.
    """
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            log.error("process_job: job %s not found", job_id)
            return

        job.status = JobStatus.PROCESSING
        db.commit()

        pending = db.scalars(
            select(Certificate)
            .where(Certificate.job_id == job_id, Certificate.status == CertStatus.PENDING)
            .order_by(Certificate.seq)
        ).all()

        for cert in pending:
            out_path = OUTPUT_DIR / job_id / f"{cert.id}.pdf"
            try:
                generate_certificate(cert.recipient_name, job.event_name, job.issued_date, out_path)
                cert.status = CertStatus.SUCCESS
                cert.file_path = str(out_path)
                cert.error_message = None
            except Exception as exc:  # noqa: BLE001 - isolate ANY per-item failure
                log.warning("certificate %s failed: %s", cert.id, exc)
                out_path.unlink(missing_ok=True)  # remove a partially written file
                cert.status = CertStatus.FAILED
                cert.error_message = f"{type(exc).__name__}: {exc}"[:500]
            db.commit()  # commit per item so progress is visible while running

        _finalize(db, job)
    except Exception:  # unexpected crash of the worker itself
        log.exception("job %s crashed", job_id)
        db.rollback()
        job = db.get(Job, job_id)
        if job is not None:
            job.status = JobStatus.FAILED
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()
