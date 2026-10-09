"""Central configuration, overridable through environment variables."""
import os
from pathlib import Path

DATABASE_URL = os.getenv("CERT_DB_URL", "sqlite:///./certificates.db")
OUTPUT_DIR = Path(os.getenv("CERT_OUTPUT_DIR", "./output"))

# Upper bound on recipients per request (protects the server from huge payloads).
MAX_RECIPIENTS = int(os.getenv("CERT_MAX_RECIPIENTS", "1000"))
