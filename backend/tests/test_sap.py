"""Integração SAP HANA: status sem segredos, teste de conexão (colunas, mapeamento, amostra), busca do KSB1 que vira
lote em Importações (mesma validação) e acesso só para "Central de dados". A conexão é simulada."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import AuditLog
from app.services import hana
from tests.conftest import login

S = "/api/v1/sap"

# colunas técnicas do SAP, como a view costuma expor
COLUMNS = ["BUKRS", "KOSTL", "KSTAR", "GJAHR", "MONAT", "BUDAT", "BELNR", "BLART", "WKGBTR", "WAERS", "SGTXT", "LIFNR"]
DATA = [
    [
        "1001",
        "1050101003",
        "6010301008",
        2026,
        1,
        date(2026, 1, 15),
        "4900000001",
        "KR",
        Decimal("1500.50"),
        "BRL",
        "ALMOCO",
        "100",
    ],
    [
        "1001",
        "1050101003",
        "6010301008",
        2026,
        2,
        date(2026, 2, 10),
        "4900000002",
        "KR",
        Decimal("2000.00"),
        "BRL",
        "CAFE",
        "100",
    ],
    [
        "2001",
        "RFM6003000",
        "6010301008",
        2026,
        1,
        date(2026, 1, 20),
        "4900000003",
        "KR",
        Decimal("300.00"),
        "BRL",
        "LANCHE",
        "200",
    ],
]


class FakeCursor:
    def __init__(self, columns, data, log):
        self.columns, self.data, self.log, self.pos = columns, data, log, 0
        self.description = [(c, "TYPE") for c in columns]

    def execute(self, sql, params=None):
        self.log.append((sql, params))
        self.rows = list(self.data)
        if params:  # filtros por parâmetro: ano e, se houver, empresa
            self.rows = [r for r in self.rows if str(r[3]) == params[0]]
            if len(params) > 1:
                self.rows = [r for r in self.rows if r[0] == params[1]]
        elif "LIMIT 0" in sql:
            self.rows = []
        elif "LIMIT" in sql:
            self.rows = self.rows[:3]
        self.pos = 0

    def fetchall(self):
        return self.rows

    def fetchmany(self, n):
        chunk, self.pos = self.rows[self.pos : self.pos + n], self.pos + n
        return chunk


class FakeConn:
    def __init__(self, columns, data, log):
        self.cur = FakeCursor(columns, data, log)
        self.closed = False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


@pytest.fixture
def hana_on(monkeypatch):
    s = get_settings()
    for k, v in (
        ("hana_host", "hana.example.invalid"),
        ("hana_user", "ro_user"),
        ("hana_password", "SENHA-SECRETA-123"),
    ):
        monkeypatch.setattr(s, k, v)
    log: list = []
    conns: list = []

    def fake_connect(timeout_ms=30000):
        conn = FakeConn(COLUMNS, DATA, log)
        conns.append(conn)
        return conn

    monkeypatch.setattr(hana, "connect", fake_connect)
    return log, conns


def test_status_hides_secrets_and_needs_configuration(client, admin):
    st = client.get(f"{S}/status", headers=admin).json()
    assert st["configured"] is False and set(st["missing"]) == {"HANA_HOST", "HANA_USER", "HANA_PASSWORD"}
    assert st["view"] == "VWG_FD_KSB1" and st["schema"] == "FD_S4HANA_PRD"
    # sem configuração a conexão falha com mensagem clara (a conexão real nunca é tentada)
    resp = client.post(f"{S}/test", headers=admin)
    assert resp.status_code == 502 and "faltam" in resp.json()["detail"]


def test_access_is_the_data_control_flag(client, admin):
    client.post(
        "/api/v1/users",
        headers=admin,
        json={"email": "c@test.com", "name": "Controladoria", "password": "Senha@12345", "roles": ["CONTROLLER"]},
    )
    other = login(client, "c@test.com", "Senha@12345")
    for method, path in (("get", "/status"), ("post", "/test")):
        assert getattr(client, method)(f"{S}{path}", headers=other).status_code == 403
    assert client.post(f"{S}/ksb1/fetch", headers=other, json={"year": 2026}).status_code == 403


def test_inspect_maps_columns_and_returns_sample_without_secrets(client, admin, hana_on):
    log, conns = hana_on
    r = client.post(f"{S}/test", headers=admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["missing"] == []
    fields = {c["name"]: c["field"] for c in body["columns"]}
    assert fields["KOSTL"] == "cost_center" and fields["KSTAR"] == "account" and fields["WKGBTR"] == "amount"
    assert fields["GJAHR"] == "year" and fields["MONAT"] == "period" and fields["BUDAT"] == "posting_date"
    assert len(body["sample"]["rows"]) == 3 and conns[0].closed
    assert "SENHA-SECRETA-123" not in r.text and "ro_user" not in r.text and "hana.example" not in r.text
    assert 'FROM "FD_S4HANA_PRD"."VWG_FD_KSB1"' in log[0][0]  # só leitura, na view configurada


def test_override_parameter_wins_and_missing_fields_are_reported(client, admin, hana_on, monkeypatch):
    columns, _ = hana.resolve_columns(
        ["VALOR_TOTAL", "CC", "CONTA", "DATA"], {"amount": "VALOR_TOTAL", "cost_center": "CC"}
    )
    assert columns["amount"] == "VALOR_TOTAL" and columns["cost_center"] == "CC"
    mapping, missing = hana.resolve_columns(["VALOR_TOTAL", "CC", "CONTA", "DATA"], {})
    assert "cost_center" in missing and "year+period ou posting_date" in missing and mapping == {}
    assert (
        client.put(
            "/api/v1/control/parameters/sap.ksb1_columns",
            headers=admin,
            json={"value": {"cost_center": "KOSTL"}, "reason": "mapeamento de teste"},
        ).status_code
        == 200
    )


def test_fetch_creates_import_batch_with_ksb1_layout(client, admin, hana_on, run_worker, db):
    log, conns = hana_on
    company = next(c for c in client.get("/api/v1/companies", headers=admin).json() if c["code"] == "1001")
    cc = client.post(
        "/api/v1/cost-centers",
        headers=admin,
        json={"company_id": company["id"], "code": "1050101003", "name": "FISCAL"},
    )
    assert cc.status_code == 201, cc.text
    r = client.post(f"{S}/ksb1/fetch", headers=admin, json={"year": 2026, "company_code": "1001"})
    assert r.status_code == 202, r.text
    batch_id = r.json()["id"]
    assert r.json()["file_name"].startswith("SAP_HANA_KSB1_2026_1001_") and r.json()["dataset_type"] == "ACTUAL"
    sql, params = log[-1]
    assert "WHERE" in sql and params == ["2026", "1001"] and "?" in sql and "1001" not in sql  # filtros por parâmetro
    assert conns[-1].closed
    run_worker()
    batch = client.get(f"/api/v1/imports/{batch_id}", headers=admin).json()
    assert batch["status"] == "VALIDATED" and batch["layout"] == "SAP_KSB1", batch
    assert batch["total_rows"] == 2 and batch["valid_rows"] == 2  # só a empresa 1001
    audit_row = db.scalar(select(AuditLog).where(AuditLog.action == "SAP_FETCH"))
    assert audit_row.after["rows"] == 2 and "SENHA" not in str(audit_row.after)
    # nada entrou na base antes da confirmação
    assert client.get("/api/v1/dashboard/inventory", headers=admin).status_code == 200


def test_fetch_validation_errors(client, admin, hana_on):
    assert client.post(f"{S}/ksb1/fetch", headers=admin, json={"year": 1999}).status_code == 422
    assert client.post(f"{S}/ksb1/fetch", headers=admin, json={"year": 2026, "company_code": "9999"}).status_code == 422
    assert client.post(f"{S}/ksb1/fetch", headers=admin, json={"year": 2030}).status_code == 404  # sem lançamentos
