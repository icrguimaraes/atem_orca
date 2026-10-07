"""Integração com o Movimentação de Pessoal: chave própria, só ativos do prefixo de CC, auditoria sem valores."""

from decimal import Decimal

from sqlalchemy import select

from app.config import get_settings
from app.models import AuditLog, Company, CostCenter, Employee, JobPosition

URL = "/api/v1/integrations/movpessoal/employees"
TOKEN = "chave-de-integracao-de-teste-com-48-caracteres-xx"


def _people(db):
    company = db.scalar(select(Company).where(Company.code == "1001"))
    if company is None:
        company = Company(code="1001", name="ATEM")
        db.add(company)
        db.flush()
    ccs = {
        code: CostCenter(company_id=company.id, code=code, name=name)
        for code, name in (("1050101002", "Contabilidade"), ("1050101003", "Fiscal"), ("2050101001", "Comercial"))
    }
    db.add_all(ccs.values())
    analyst = JobPosition(name="Analista Sintético PL")
    db.add(analyst)
    db.flush()
    db.add_all(
        [
            Employee(
                registration="9001",
                name="PESSOA SINTETICA A",
                company_id=company.id,
                cost_center_id=ccs["1050101002"].id,
                position_id=analyst.id,
                base_salary=Decimal("5000.5"),
            ),
            Employee(
                registration="9002",
                name="PESSOA SINTETICA B",
                company_id=company.id,
                cost_center_id=ccs["1050101003"].id,
                base_salary=Decimal("7000"),
            ),
            Employee(
                registration="9003",
                name="PESSOA INATIVA",
                company_id=company.id,
                cost_center_id=ccs["1050101003"].id,
                base_salary=Decimal("1"),
                is_active=False,
            ),
            Employee(
                registration="9004",
                name="PESSOA OUTRA AREA",
                company_id=company.id,
                cost_center_id=ccs["2050101001"].id,
                base_salary=Decimal("1"),
            ),
        ]
    )
    db.commit()


def test_movpessoal_employees(client, admin, db, monkeypatch):
    _people(db)
    settings = get_settings()
    monkeypatch.setattr(settings, "movpessoal_token", None)
    assert client.get(URL, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404  # sem chave: desligada

    monkeypatch.setattr(settings, "movpessoal_token", TOKEN)
    assert client.get(URL).status_code == 401
    assert client.get(URL, headers={"Authorization": "Bearer errada"}).status_code == 401
    assert client.get(URL, headers=admin).status_code == 401  # login de usuário não serve: é chave de máquina
    assert client.get(URL, headers={"Authorization": f"Bearer {TOKEN}"}, params={"cc_prefix": "x"}).status_code == 422

    resp = client.get(URL, headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200, resp.text
    rows = resp.json()["employees"]
    assert [(r["registration"], r["cost_center_code"]) for r in rows] == [
        ("9001", "1050101002"),
        ("9002", "1050101003"),
    ]
    assert rows[0] | {"admission_date": None} == {
        "company_code": "1001",
        "registration": "9001",
        "name": "PESSOA SINTETICA A",
        "cost_center_code": "1050101002",
        "job_title": "Analista Sintético PL",
        "base_salary": "5000.50",
        "contract_type": "CLT",
        "admission_date": None,
    }
    log = db.scalars(select(AuditLog).where(AuditLog.action == "INTEGRATION_READ")).all()
    assert len(log) == 1 and log[0].after == {"consumer": "movpessoal", "cc_prefix": "1050101", "count": 2}
