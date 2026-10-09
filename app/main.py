import io
import re
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app import services
from app.database import Base, engine, get_db
from app.models import Certificate, CertStatus, Job
from app.schemas import CertificateOut, JobCreate, JobCreated, JobOut, JobSummary


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)  # create tables on startup
    yield


app = FastAPI(title="Bulk Certificate Generator", version="1.0.0", lifespan=lifespan)


def _get_job_or_404(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _cert_out(job_id: str, cert: Certificate) -> CertificateOut:
    out = CertificateOut.model_validate(cert)
    if cert.status == CertStatus.SUCCESS:
        out.download_url = f"/jobs/{job_id}/certificates/{cert.id}"
    return out


@app.post("/jobs", response_model=JobCreated, status_code=202)
def create_job(payload: JobCreate, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Accept a bulk request, validate recipients, and queue generation."""
    job = services.create_job(db, payload)
    background.add_task(services.process_job, job.id)
    return JobCreated(
        job_id=job.id, status=job.status, total=job.total, status_url=f"/jobs/{job.id}"
    )


@app.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    """Progress + per-recipient results (successes and failures with reasons)."""
    job = _get_job_or_404(db, job_id)
    counts = services.count_by_status(db, job.id)
    return JobOut(
        job_id=job.id,
        event_name=job.event_name,
        issued_date=job.issued_date,
        status=job.status,
        created_at=job.created_at,
        completed_at=job.completed_at,
        summary=JobSummary(
            total=job.total,
            succeeded=counts[CertStatus.SUCCESS],
            failed=counts[CertStatus.FAILED],
            pending=counts[CertStatus.PENDING],
        ),
        download_all_url=(
            f"/jobs/{job.id}/download" if counts[CertStatus.SUCCESS] else None
        ),
        certificates=[_cert_out(job.id, c) for c in job.certificates],
    )


@app.get("/jobs/{job_id}/certificates/{cert_id}")
def download_certificate(job_id: str, cert_id: str, db: Session = Depends(get_db)):
    """Download a single certificate PDF."""
    _get_job_or_404(db, job_id)
    cert = db.get(Certificate, cert_id)
    if cert is None or cert.job_id != job_id:
        raise HTTPException(status_code=404, detail="Certificate not found")
    if cert.status != CertStatus.SUCCESS or not cert.file_path:
        raise HTTPException(
            status_code=409, detail=f"Certificate is not available (status: {cert.status})"
        )
    path = Path(cert.file_path)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="Certificate file is no longer on disk")
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", cert.recipient_name).strip("_") or "certificate"
    return FileResponse(path, media_type="application/pdf", filename=f"{safe}.pdf")


@app.get("/jobs/{job_id}/download")
def download_all(job_id: str, db: Session = Depends(get_db)):
    """Download every successfully generated certificate of a job as a ZIP."""
    job = _get_job_or_404(db, job_id)
    ok = [c for c in job.certificates if c.status == CertStatus.SUCCESS and c.file_path]
    if not ok:
        raise HTTPException(status_code=409, detail="No certificates are available yet")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for c in ok:
            path = Path(c.file_path)
            if not path.is_file():
                continue
            safe = re.sub(r"[^A-Za-z0-9_-]+", "_", c.recipient_name).strip("_") or "certificate"
            zf.write(path, arcname=f"{safe}_{c.id[:8]}.pdf")  # id suffix keeps names unique
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="certificates_{job_id}.zip"'},
    )
