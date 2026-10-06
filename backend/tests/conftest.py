import os
import tempfile

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://postgres@/atem_test?host=/tmp&port=5433")
os.environ["RUN_IMPORT_WORKER"] = "false"
os.environ["UPLOAD_DIR"] = tempfile.mkdtemp(prefix="atem_uploads_")
os.environ["ADMIN_PASSWORD"] = "Admin@12345"
os.environ["ADMIN_EMAIL"] = "admin@test.com"
os.environ["SECRET_KEY"] = "test-secret-key-with-at-least-32-bytes!!"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.v1.auth import reset_rate_limit  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.imports.pipeline import process_pending  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from app.seed import seed  # noqa: E402
from app.services.storage import get_storage  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
    reset_rate_limit()
    yield


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    return TestClient(app)


def login(client: TestClient, email: str, password: str) -> dict:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
def admin(client):
    return login(client, "admin@test.com", "Admin@12345")


@pytest.fixture
def run_worker():
    def _run():
        with SessionLocal() as session:
            return process_pending(session, get_storage(), limit=20)

    return _run
