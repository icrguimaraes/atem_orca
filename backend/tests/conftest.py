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
from sqlalchemy import text  # noqa: E402

from app.api.v1.auth import reset_rate_limit  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.imports.pipeline import process_pending  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from app.seed import seed  # noqa: E402
from app.services.storage import get_storage  # noqa: E402


def rebuild_schema() -> None:
    """Esquema do zero + seed (uma vez por sessão; de novo só depois dos testes de migração)."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)


@pytest.fixture(scope="session", autouse=True)
def schema():
    rebuild_schema()
    yield


@pytest.fixture(autouse=True)
def fresh_db(request):
    """Cada teste roda numa transação desfeita no fim (13/10/2026: antes o banco era recriado a cada teste, ~60% do
    tempo da suíte). Toda `SessionLocal()` da aplicação passa a usar a mesma conexão; `commit()` vira um savepoint.
    Testes marcados com `rebuild_schema` (migrações) rodam fora da transação e recriam o esquema ao terminar."""
    reset_rate_limit()
    if request.node.get_closest_marker("rebuild_schema"):
        yield
        rebuild_schema()
        return
    with engine.connect() as conn:
        trans = conn.begin()
        SessionLocal.configure(bind=conn, join_transaction_mode="create_savepoint")
        try:
            yield
        finally:
            SessionLocal.configure(bind=engine)
            trans.rollback()
    reset_sequences()


def reset_sequences() -> None:
    """Sequências de id voltam a max(id)+1: o rollback não as desfaz e alguns testes usam ids fixos (ex.: o CC
    criado pelo template é o 1), como quando o banco era recriado."""
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if "id" in table.c and table.c.id.autoincrement is not False:
                conn.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                        f"coalesce((SELECT max(id) FROM {table.name}), 1), "
                        f"(SELECT max(id) FROM {table.name}) IS NOT NULL)"
                    )
                )


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
