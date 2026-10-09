# Bulk Certificate Generator (Backend API)

A FastAPI service that accepts one request containing many recipients, generates
a PDF certificate for each valid recipient from a single predefined template,
tracks progress per job, and lets the client download the results.

**Stack:** Python 3.10+, FastAPI, SQLAlchemy 2 + SQLite, ReportLab, pytest.

## 1. Setup

```bash
git clone <your-repo-url> && cd certificate-generator
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Run the application

```bash
uvicorn app.main:app --reload
```

- API: http://127.0.0.1:8000
- Interactive docs (Swagger): http://127.0.0.1:8000/docs

Tables are created automatically on startup. Generated PDFs are written to
`./output/<job_id>/`.

Optional environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `CERT_DB_URL` | `sqlite:///./certificates.db` | Any SQLAlchemy URL (e.g. PostgreSQL) |
| `CERT_OUTPUT_DIR` | `./output` | Where PDFs are stored |
| `CERT_MAX_RECIPIENTS` | `1000` | Max recipients per request |

# Deployed on Render 
https://certificate-generator-1-kv5u.onrender.com/docs

## 3. Run the tests

```bash
pytest -v
```

Tests use a temporary database and output folder, so they never touch real data.

## 4. Submit a certificate generation request

```bash
curl -X POST http://127.0.0.1:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "event_name": "Python Workshop 2026",
    "issued_date": "2026-10-09",
    "recipients": [
      {"name": "Asha Rao",   "email": "asha@example.com"},
      {"name": "Ravi Kumar", "email": "ravi@example.com"}
    ]
  }'
```

Response (`202 Accepted`):

```json
{"job_id": "019d...", "status": "PENDING", "total": 2, "status_url": "/jobs/019d..."}
```

`issued_date` is optional (defaults to today).

### Check progress / results

```bash
curl http://127.0.0.1:8000/jobs/<job_id>
```

Returns the job `status`, a `summary` (`total / succeeded / failed / pending`),
and every recipient with its own `status`, `error_message` (for failures) and
`download_url` (for successes).

Job statuses: `PENDING` -> `PROCESSING` -> `COMPLETED` (all OK) |
`COMPLETED_WITH_ERRORS` (some failed) | `FAILED` (none succeeded).

## 5. Retrieve generated certificates

| Endpoint | Returns |
|---|---|
| `GET /jobs/{job_id}/certificates/{cert_id}` | One certificate as a PDF |
| `GET /jobs/{job_id}/download` | All successful certificates of the job as a ZIP |

```bash
curl -OJ http://127.0.0.1:8000/jobs/<job_id>/download
```

Error codes: `404` unknown job/certificate, `409` certificate not available
(failed/pending), `410` file missing on disk, `422` invalid request body.

## 6. Design decisions

**Background processing (not synchronous).** `POST /jobs` stores the job and
returns `202` immediately; generation runs in a FastAPI `BackgroundTask`. A
request with hundreds of recipients would otherwise hold the HTTP connection
open and risk client/proxy timeouts. The client polls `GET /jobs/{id}`.
*Trade-off:* background tasks live inside the web process, so a job that is
mid-flight when the server restarts will not resume. For production I would
move `process_job` to a real queue (Celery/RQ + Redis) with retries; the logic
is isolated in `services.process_job(job_id)` so that swap is small.

**Per-recipient failure isolation.** Each certificate is generated inside its
own `try/except`; the error is stored on that certificate and the loop
continues. The job ends as `COMPLETED_WITH_ERRORS` (or `FAILED` if nothing
succeeded). Progress is committed after every certificate, so status is live.

**Two-layer validation.**
1. Request level (Pydantic): malformed body, empty/oversized recipient list,
   blank `event_name`, bad date -> whole request rejected with `422`.
2. Recipient level (`services.validate_recipient`): empty/too-long name,
   missing/invalid email -> only that recipient is stored as `FAILED` with a
   reason. Valid recipients in the same request are still generated.

**Storage.** Job and per-certificate rows live in a relational DB (SQLite by
default; switch via `CERT_DB_URL`). PDFs are stored on disk under the
certificate's UUID, never under the user-supplied name, which avoids filename
collisions and path-traversal issues. Download filenames are sanitized.

**Template.** One hard-coded template in `app/certificate.py` (ReportLab). Long
names/events are auto-shrunk to fit one line.

**Known limitations.**
- The built-in PDF font only supports Latin-1 characters; names in other
  scripts (e.g. Devanagari) are reported as a failed certificate for that
  recipient. Fix: register a Unicode TTF font in ReportLab.
- No authentication or rate limiting.
- The ZIP is built in memory; for very large jobs stream it instead.
- No duplicate-email detection.

## Project layout

```
app/
  main.py         # routes
  services.py     # create_job, validate_recipient, process_job (background)
  certificate.py  # PDF template
  models.py       # Job, Certificate tables
  schemas.py      # request/response models
  database.py     # engine + session
  config.py       # env-based settings
tests/
  conftest.py     # temp DB/output, fixtures
  test_jobs.py    # all test cases
```
