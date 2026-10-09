import io
import zipfile
from datetime import date

import pytest

from app import services
from app.certificate import generate_certificate


# ---------- 1. Creating a generation job ----------
def test_create_job_returns_202_and_job_id(client, valid_payload):
    r = client.post("/jobs", json=valid_payload)
    assert r.status_code == 202
    body = r.json()
    assert body["job_id"]
    assert body["total"] == 3
    assert body["status_url"] == f"/jobs/{body['job_id']}"


def test_unknown_job_returns_404(client):
    assert client.get("/jobs/doesnotexist").status_code == 404


# ---------- 2. Input validation ----------
@pytest.mark.parametrize(
    "payload",
    [
        {"event_name": "X", "recipients": []},                       # empty list
        {"event_name": "   ", "recipients": [{"name": "A", "email": "a@b.co"}]},  # blank event
        {"recipients": [{"name": "A", "email": "a@b.co"}]},          # missing event_name
        {"event_name": "X"},                                          # missing recipients
        {"event_name": "X", "recipients": "nope"},                    # wrong type
        {"event_name": "X", "issued_date": "not-a-date",
         "recipients": [{"name": "A", "email": "a@b.co"}]},          # bad date
    ],
)
def test_request_level_validation_rejected(client, payload):
    assert client.post("/jobs", json=payload).status_code == 422


def test_too_many_recipients_rejected(client, monkeypatch):
    from app import schemas

    too_many = [{"name": f"P{i}", "email": f"p{i}@x.io"} for i in range(schemas.MAX_RECIPIENTS + 1)]
    r = client.post("/jobs", json={"event_name": "Big", "recipients": too_many})
    assert r.status_code == 422


def test_invalid_recipients_are_marked_failed_not_rejected(client):
    payload = {
        "event_name": "Hackathon",
        "recipients": [
            {"name": "Good Person", "email": "good@example.com"},
            {"name": "", "email": "noname@example.com"},
            {"name": "Bad Email", "email": "not-an-email"},
            {"name": "No Email"},
        ],
    }
    job_id = client.post("/jobs", json=payload).json()["job_id"]
    body = client.get(f"/jobs/{job_id}").json()

    assert body["summary"] == {"total": 4, "succeeded": 1, "failed": 3, "pending": 0}
    assert body["status"] == "COMPLETED_WITH_ERRORS"
    errors = [c["error_message"] for c in body["certificates"] if c["status"] == "FAILED"]
    assert "name is required" in errors
    assert "email is not a valid address" in errors
    assert "email is required" in errors


def test_validate_recipient_unit():
    from app.schemas import RecipientIn

    assert services.validate_recipient(RecipientIn(name="A", email="a@b.co")) is None
    assert services.validate_recipient(RecipientIn(name="A" * 101, email="a@b.co"))
    assert services.validate_recipient(RecipientIn(name="A", email="a@b"))


# ---------- 3. Certificate generation ----------
def test_generate_certificate_creates_pdf(tmp_path):
    out = generate_certificate("Asha Rao", "Python Workshop", date(2026, 10, 9), tmp_path / "c.pdf")
    data = out.read_bytes()
    assert data.startswith(b"%PDF")
    assert len(data) > 500


def test_generate_certificate_long_name_still_works(tmp_path):
    out = generate_certificate("W" * 100, "E" * 200, date(2026, 1, 1), tmp_path / "long.pdf")
    assert out.read_bytes().startswith(b"%PDF")


def test_generate_certificate_rejects_unsupported_characters(tmp_path):
    with pytest.raises(UnicodeEncodeError):
        generate_certificate("आशा राव", "Event", date(2026, 1, 1), tmp_path / "x.pdf")


# ---------- 4. Job status / progress ----------
def test_job_status_counts_add_up(client, valid_payload):
    job_id = client.post("/jobs", json=valid_payload).json()["job_id"]
    body = client.get(f"/jobs/{job_id}").json()

    assert body["status"] == "COMPLETED"
    s = body["summary"]
    assert s == {"total": 3, "succeeded": 3, "failed": 0, "pending": 0}
    assert s["succeeded"] + s["failed"] + s["pending"] == s["total"]
    assert body["completed_at"] is not None
    assert body["download_all_url"] == f"/jobs/{job_id}/download"
    assert [c["recipient_name"] for c in body["certificates"]] == [
        "Asha Rao", "Ravi Kumar", "Meera Iyer",
    ]  # original order preserved


# ---------- 5. Handling an individual certificate failure ----------
def test_one_failure_does_not_block_others(client, valid_payload, monkeypatch):
    real = services.generate_certificate

    def flaky(name, event, issued, path):
        if name == "Ravi Kumar":
            raise RuntimeError("disk exploded")
        return real(name, event, issued, path)

    monkeypatch.setattr(services, "generate_certificate", flaky)

    job_id = client.post("/jobs", json=valid_payload).json()["job_id"]
    body = client.get(f"/jobs/{job_id}").json()

    assert body["status"] == "COMPLETED_WITH_ERRORS"
    assert body["summary"] == {"total": 3, "succeeded": 2, "failed": 1, "pending": 0}
    by_name = {c["recipient_name"]: c for c in body["certificates"]}
    assert by_name["Ravi Kumar"]["status"] == "FAILED"
    assert "disk exploded" in by_name["Ravi Kumar"]["error_message"]
    assert by_name["Ravi Kumar"]["download_url"] is None
    assert by_name["Asha Rao"]["status"] == "SUCCESS"
    assert by_name["Meera Iyer"]["status"] == "SUCCESS"


def test_all_failed_marks_job_failed(client, valid_payload, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr(services, "generate_certificate", boom)
    job_id = client.post("/jobs", json=valid_payload).json()["job_id"]
    body = client.get(f"/jobs/{job_id}").json()
    assert body["status"] == "FAILED"
    assert body["download_all_url"] is None


def test_unsupported_characters_fail_only_that_recipient(client):
    payload = {
        "event_name": "Intl Event",
        "recipients": [
            {"name": "Asha Rao", "email": "asha@example.com"},
            {"name": "आशा राव", "email": "asha2@example.com"},
        ],
    }
    job_id = client.post("/jobs", json=payload).json()["job_id"]
    body = client.get(f"/jobs/{job_id}").json()
    assert body["summary"]["succeeded"] == 1
    assert body["summary"]["failed"] == 1


# ---------- 6. Retrieving generated certificates ----------
def test_download_single_certificate(client, valid_payload):
    job_id = client.post("/jobs", json=valid_payload).json()["job_id"]
    cert = client.get(f"/jobs/{job_id}").json()["certificates"][0]

    r = client.get(cert["download_url"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    assert "Asha_Rao.pdf" in r.headers["content-disposition"]


def test_download_all_as_zip(client, valid_payload):
    job_id = client.post("/jobs", json=valid_payload).json()["job_id"]
    r = client.get(f"/jobs/{job_id}/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        assert len(names) == 3
        assert all(n.endswith(".pdf") for n in names)
        assert all(zf.read(n).startswith(b"%PDF") for n in names)


def test_zip_only_contains_successful_certificates(client, valid_payload, monkeypatch):
    real = services.generate_certificate

    def flaky(name, event, issued, path):
        if name == "Ravi Kumar":
            raise RuntimeError("fail")
        return real(name, event, issued, path)

    monkeypatch.setattr(services, "generate_certificate", flaky)
    job_id = client.post("/jobs", json=valid_payload).json()["job_id"]
    r = client.get(f"/jobs/{job_id}/download")
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert len(zf.namelist()) == 2


def test_retrieval_error_cases(client, valid_payload, monkeypatch):
    # unknown job
    assert client.get("/jobs/nope/download").status_code == 404
    assert client.get("/jobs/nope/certificates/nope").status_code == 404

    job_id = client.post("/jobs", json=valid_payload).json()["job_id"]
    # unknown certificate in a real job
    assert client.get(f"/jobs/{job_id}/certificates/nope").status_code == 404

    # a FAILED certificate is not downloadable (409)
    bad = client.post(
        "/jobs", json={"event_name": "E", "recipients": [{"name": "", "email": "x"}]}
    ).json()["job_id"]
    cert_id = client.get(f"/jobs/{bad}").json()["certificates"][0]["id"]
    assert client.get(f"/jobs/{bad}/certificates/{cert_id}").status_code == 409
    assert client.get(f"/jobs/{bad}/download").status_code == 409


def test_certificate_cannot_be_fetched_through_another_job(client, valid_payload):
    job_a = client.post("/jobs", json=valid_payload).json()["job_id"]
    job_b = client.post("/jobs", json=valid_payload).json()["job_id"]
    cert_of_a = client.get(f"/jobs/{job_a}").json()["certificates"][0]["id"]
    assert client.get(f"/jobs/{job_b}/certificates/{cert_of_a}").status_code == 404
