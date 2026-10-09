import os
import shutil
import tempfile

# IMPORTANT: set env vars BEFORE importing the app so it uses a throwaway DB/output dir.
_TMP = tempfile.mkdtemp(prefix="certtests_")
os.environ["CERT_DB_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["CERT_OUTPUT_DIR"] = f"{_TMP}/output"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import OUTPUT_DIR  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state():
    """Fresh tables and an empty output directory for every test."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    shutil.rmtree(OUTPUT_DIR, ignore_errors=True)
    yield


@pytest.fixture
def client():
    # TestClient runs FastAPI background tasks before the call returns,
    # so a job is already finished when we query its status.
    return TestClient(app)


@pytest.fixture
def valid_payload():
    return {
        "event_name": "Python Workshop 2026",
        "issued_date": "2026-10-09",
        "recipients": [
            {"name": "Asha Rao", "email": "asha@example.com"},
            {"name": "Ravi Kumar", "email": "ravi@example.com"},
            {"name": "Meera Iyer", "email": "meera@example.com"},
        ],
    }
