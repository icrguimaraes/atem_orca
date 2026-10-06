import io
from datetime import datetime
from decimal import Decimal

from openpyxl import load_workbook
from sqlalchemy import func, select

from app.models import (
    Account,
    AccountDetail,
    ActualEntry,
    AuditLog,
    Branch,
    BudgetPackage,
    CostCenter,
    DatasetVersion,
    Employee,
    MacroAssumption,
)
from tests import builders

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def upload(client, headers, content: bytes, name: str, **form):
    mime = "text/csv" if name.endswith(".csv") else XLSX
    resp = client.post(
        "/api/v1/imports",
        headers=headers,
        files={"file": (name, content, mime)},
        data={k: str(v) for k, v in form.items()},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["id"]


def status(client, headers, batch_id):
    return client.get(f"/api/v1/imports/{batch_id}", headers=headers).json()


def import_and_load(client, headers, run_worker, content, name, force=False, **form):
    batch_id = upload(client, headers, content, name, **form)
    run_worker()
    assert status(client, headers, batch_id)["status"] == "VALIDATED", status(client, headers, batch_id)
    resp = client.post(f"/api/v1/imports/{batch_id}/confirm", headers=headers, params={"force": force})
    assert resp.status_code == 200, resp.text
    run_worker()
    final = status(client, headers, batch_id)
    assert final["status"] == "COMPLETED", final
    return final


def test_master_data_from_template_bd(client, admin, run_worker, db):
    batch_id = upload(client, admin, builders.opex_template_bd(), "Template_OPEX.xlsx")
    run_worker()
    batch = status(client, admin, batch_id)
    assert batch["dataset_type"] == "MASTER_DATA" and batch["layout"] == "TEMPLATE_BD"
    assert batch["total_rows"] == 9 and batch["error_rows"] == 1  # conta "ABC"
    preview = client.get(f"/api/v1/imports/{batch_id}/preview", headers=admin).json()
    codes = {e["code"] for e in preview["errors_by_code"]}
    assert {"INVALID_CODE", "NEW_PACKAGE"} <= codes
    assert client.post(f"/api/v1/imports/{batch_id}/confirm", headers=admin).status_code == 200
    run_worker()
    assert status(client, admin, batch_id)["status"] == "COMPLETED"

    assert db.scalar(select(CostCenter).where(CostCenter.code == "1050101012")).name == "CENTRO NOVO"
    assert db.scalar(select(Branch).where(Branch.code == "0099")) is not None
    assert db.scalar(select(BudgetPackage).where(BudgetPackage.name == "Pacote Inédito")) is not None
    new_acc = db.scalar(select(Account).where(Account.code == "6010309999"))
    assert new_acc.nature == "OPEX"
    dti = db.scalar(select(Account).where(Account.code == "6010301002"))
    assert db.scalar(select(AccountDetail).where(AccountDetail.account_id == dti.id)).name == "Links de Internet"
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.entity_type == "cost_center")) >= 2


def _seed_cc(client, admin, run_worker):
    import_and_load(client, admin, run_worker, builders.opex_template_bd(), "bd.xlsx", dataset_type="MASTER_DATA")


def test_actual_wide_validation_counts_and_versioning(client, admin, run_worker, db):
    _seed_cc(client, admin, run_worker)
    rows = [
        (
            "1001",
            "0001",
            "MANAUS",
            "1050101011",
            "CC",
            "Gestor",
            "6010301001",
            "Hospedagem",
            "Viagens",
            100,
            200,
            None,
            None,
            None,
            None,
            None,
            50,
            350,
        ),
        (
            "1001",
            "0001",
            "MANAUS",
            "1050101011",
            "CC",
            "Gestor",
            "6010301002",
            "Telefonia",
            "DTI",
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            10,
            999,
        ),  # total divergente → aviso
        (
            "1001",
            "0001",
            "MANAUS",
            "1050101011",
            "CC",
            "Gestor",
            "6010301001",
            "Hospedagem",
            "Viagens",
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            8,
        ),  # duplicado
        (
            "1001",
            "0001",
            "MANAUS",
            "9999999999",
            "CC X",
            "Gestor",
            "6010301001",
            "Hospedagem",
            "Viagens",
            5,
            5,
            5,
            5,
            5,
            5,
            5,
            5,
            40,
        ),  # CC inexistente
        (
            "1001",
            "0001",
            "MANAUS",
            "1050101011",
            "CC",
            "Gestor",
            "6010301003",
            "Energia",
            "Consumo",
            "abc",
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            7,
        ),  # número inválido
        (None, None, None, None, None, None, None, None, None, None),  # linha vazia ignorada
    ]
    content = builders.realizado_wide(rows)
    batch_id = upload(client, admin, content, "realizado_2026.xlsx", dataset_type="ACTUAL")
    run_worker()
    b = status(client, admin, batch_id)
    assert (b["total_rows"], b["valid_rows"], b["error_rows"], b["duplicate_rows"], b["warning_rows"]) == (
        5,
        2,
        2,
        1,
        1,
    )
    assert b["summary"]["meta"]["year"] == 2026
    codes_preview = {
        e["code"] for e in client.get(f"/api/v1/imports/{batch_id}/preview", headers=admin).json()["errors_by_code"]
    }
    assert "UNKNOWN_COLUMNS" not in codes_preview  # cabeçalhos de mês (datas) não são colunas desconhecidas
    report = load_workbook(io.BytesIO(client.get(f"/api/v1/imports/{batch_id}/errors.xlsx", headers=admin).content))
    codes = {r[4] for r in report["Inconsistências"].iter_rows(min_row=2, values_only=True)}
    assert {"UNKNOWN_COST_CENTER", "INVALID_NUMBER", "DUPLICATE", "TOTAL_MISMATCH"} <= codes

    client.post(f"/api/v1/imports/{batch_id}/confirm", headers=admin)
    run_worker()
    v1 = db.scalar(select(DatasetVersion).where(DatasetVersion.scope_key == "ACTUAL:2026:1001"))
    assert v1.version_number == 1 and v1.is_current and v1.last_closed_period == 8
    total_v1 = db.scalar(select(func.sum(ActualEntry.amount)).where(ActualEntry.dataset_version_id == v1.id))
    assert total_v1 == Decimal("430.00")  # 350 + 80

    # nova carga do mesmo escopo → versão 2; versão 1 preservada e não corrente
    import_and_load(
        client,
        admin,
        run_worker,
        builders.realizado_wide([rows[0]]),
        "realizado_v2.xlsx",
        force=True,
        dataset_type="ACTUAL",
    )
    db.expire_all()
    versions = db.scalars(
        select(DatasetVersion)
        .where(DatasetVersion.scope_key == "ACTUAL:2026:1001")
        .order_by(DatasetVersion.version_number)
    ).all()
    assert [(v.version_number, v.is_current) for v in versions] == [(1, False), (2, True)]
    assert versions[0].superseded_at is not None
    assert db.scalar(select(func.count()).select_from(ActualEntry).where(ActualEntry.dataset_version_id == v1.id)) == 11


def test_actual_create_missing_dimensions(client, admin, run_worker, db):
    rows = [
        (
            "1001",
            "0001",
            "MANAUS",
            "7777777777",
            "CC CRIADO",
            "Fulano",
            "6010388888",
            "Conta criada",
            "Viagens",
            10,
            20,
            None,
            None,
            None,
            None,
            None,
            None,
            30,
        )
    ]
    import_and_load(
        client,
        admin,
        run_worker,
        builders.realizado_wide(rows),
        "r.xlsx",
        dataset_type="ACTUAL",
        create_missing_dimensions="true",
    )
    assert db.scalar(select(CostCenter).where(CostCenter.code == "7777777777")).manager_name == "Fulano"
    assert db.scalar(select(Account).where(Account.code == "6010388888")) is not None


def test_ksb1_csv(client, admin, run_worker, db):
    _seed_cc(client, admin, run_worker)
    lines = [
        (
            "1001",
            "1050101011",
            "6010301001",
            "Hospedagem",
            2025,
            3,
            "15/03/2025",
            "5100001",
            "1.234,56",
            "BRL",
            "Hotel",
            "100200",
            "HOTEL X",
        ),
        (
            "1001",
            "1050101011",
            "6010301001",
            "Hospedagem",
            2025,
            14,
            "31/12/2025",
            "5100002",
            "100,00",
            "BRL",
            "Ajuste",
            None,
            None,
        ),
        (
            "1001",
            "1050101011",
            "6010301001",
            "Hospedagem",
            2025,
            3,
            "15/03/2025",
            "5100001",
            "1.234,56",
            "BRL",
            "Hotel",
            "100200",
            "HOTEL X",
        ),  # partida idêntica à primeira: legítima no KSB1 (não é deduplicada)
        ("1001", None, None, None, None, None, None, None, "1.334,56", None, "Total", None, None),  # subtotal
    ]
    batch_id = upload(client, admin, builders.ksb1_csv(lines), "ksb1_2025.csv")
    run_worker()
    b = status(client, admin, batch_id)
    assert b["layout"] == "SAP_KSB1" and b["dataset_type"] == "ACTUAL"
    assert (b["valid_rows"], b["duplicate_rows"]) == (3, 0)
    client.post(f"/api/v1/imports/{batch_id}/confirm", headers=admin)
    run_worker()
    entries = db.scalars(select(ActualEntry).order_by(ActualEntry.period)).all()
    assert [(e.period, e.amount, e.source) for e in entries] == [
        (3, Decimal("1234.56"), "SAP_KSB1"),
        (3, Decimal("1234.56"), "SAP_KSB1"),
        (12, Decimal("100.00"), "SAP_KSB1"),
    ]
    assert entries[0].vendor_name == "HOTEL X" and entries[0].document_number == "5100001"


def test_ksb1_sap_export_xlsx(client, admin, run_worker, db):
    """Layout real da exportação KSB1: cabeçalhos abreviados, planta, duas datas, subtotais e total geral."""
    _seed_cc(client, admin, run_worker)
    rows = [
        # período pela Data de lançamento (setembro), não pela Data do documento (agosto)
        {
            "cost_center": "1050101011",
            "account": "6010301001",
            "amount": 117.62,
            "document_date": datetime(2026, 8, 31),
            "posting_date": datetime(2026, 9, 2),
        },
        {
            "cost_center": "1050101011",
            "account": "6010301001",
            "amount": 117.62,
            "document_date": datetime(2026, 8, 31),
            "posting_date": datetime(2026, 9, 2),
        },  # idêntica: as duas partidas entram
        {
            "cost_center": "1050101011",
            "account": "6010301001",
            "plant": "C999",  # planta sem filial correspondente: aviso, fica sem filial
            "amount": 100,
            "document_date": datetime(2026, 1, 10),
            "posting_date": datetime(2026, 1, 10),
        },
        {
            "cost_center": "1050101011",
            "account": "6010399999",  # conta nova: criada com o nome do arquivo
            "account_name": "Conta Nova KSB1",
            "plant": None,
            "amount": 50,
            "document_date": datetime(2025, 12, 31),
            "posting_date": datetime(2025, 12, 31),
        },
    ]
    content = builders.ksb1_export_xlsx(rows, subtotals={"1050101011": 385.24}, total=385.24)
    batch_id = upload(client, admin, content, "KSB1 2025 a 2026.xlsx", create_missing_dimensions="true")
    run_worker()
    b = status(client, admin, batch_id)
    assert b["status"] == "VALIDATED", b
    assert b["layout"] == "SAP_KSB1" and b["dataset_type"] == "ACTUAL"  # detecção automática
    assert (b["valid_rows"], b["duplicate_rows"], b["error_rows"]) == (4, 0, 0)  # subtotal e total ignorados
    # avisos de cadastro valem uma vez por código (conta nova e planta desconhecida), não por partida
    assert b["warning_rows"] == 2
    assert b["summary"]["new_accounts"] == [{"code": "6010399999", "name": "Conta Nova KSB1"}]
    assert "new_cost_centers" in b["summary"] and b["summary"]["new_cost_centers"] == []
    resp = client.post(f"/api/v1/imports/{batch_id}/confirm", headers=admin)
    assert resp.status_code == 200, resp.text
    run_worker()
    assert status(client, admin, batch_id)["status"] == "COMPLETED"

    manaus = db.scalar(select(Branch.id).where(Branch.code == "0001"))
    entries = db.scalars(
        select(ActualEntry).order_by(ActualEntry.fiscal_year, ActualEntry.period, ActualEntry.id)
    ).all()
    assert [(e.fiscal_year, e.period, e.amount, e.branch_id) for e in entries] == [
        (2025, 12, Decimal("50.00"), None),
        (2026, 1, Decimal("100.00"), None),
        (2026, 9, Decimal("117.62"), manaus),
        (2026, 9, Decimal("117.62"), manaus),
    ]
    assert entries[-1].document_number == "5100001" and entries[-1].source == "SAP_KSB1"
    new_account = db.scalar(select(Account).where(Account.code == "6010399999"))
    assert new_account is not None and new_account.name == "Conta Nova KSB1" and new_account.nature == "OPEX"
    closed = {
        v.scope_key: v.last_closed_period
        for v in db.scalars(select(DatasetVersion).where(DatasetVersion.dataset_type == "ACTUAL"))
    }
    assert closed == {"ACTUAL:2025:1001": 12, "ACTUAL:2026:1001": 9}  # 2026 fecha em setembro


def test_employees_and_vacancy(client, admin, run_worker, db):
    _seed_cc(client, admin, run_worker)
    rows = [
        (
            "100101455",
            "COLABORADOR A",
            "ANALISTA",
            "1001",
            "0001",
            "1050101011",
            10000,
            "PROMOVER",
            7,
            None,
            10500,
            "CLT",
            "NÃO",
            "SIM",
        ),
        (
            "100101456",
            "COLABORADOR B",
            "CONSULTOR",
            "1001",
            "0001",
            "1050101011",
            15000,
            "MANTER",
            None,
            None,
            None,
            "PJ",
            None,
            None,
        ),
        (
            None,
            "VAGA EM ABERTO - SUBSTITUIÇÃO",
            "ANALISTA",
            "1001",
            None,
            "1050101011",
            9000,
            "INCLUIR",
            3,
            None,
            9000,
            "CLT",
            None,
            None,
        ),
        (
            "100101457",
            "COLABORADOR C",
            "ANALISTA",
            "1001",
            None,
            "1050101011",
            8000,
            "PROMOVER",
            None,
            None,
            None,
            "CLT",
            None,
            None,
        ),  # promoção sem mês/novo salário → erro
        (
            "100101458",
            "COLABORADOR D",
            "ANALISTA",
            "1001",
            None,
            "1050101011",
            8000,
            None,
            None,
            None,
            None,
            "AUTONOMO",
            None,
            None,
        ),  # contrato não parametrizado
    ]
    batch_id = upload(client, admin, builders.quadro_funcionarios(rows), "Template Pessoal.xlsx")
    run_worker()
    b = status(client, admin, batch_id)
    assert b["dataset_type"] == "EMPLOYEES"
    assert (b["total_rows"], b["valid_rows"], b["error_rows"]) == (5, 3, 2)
    client.post(f"/api/v1/imports/{batch_id}/confirm", headers=admin)
    run_worker()
    final = status(client, admin, batch_id)
    assert final["summary"]["load"]["employees_created"] == 2
    movements = final["summary"]["load"]["movements"]
    assert movements.get("hires", 0) + movements.get("without_cost_center", 0) == 1  # vaga vira contratação
    pj = db.scalar(select(Employee).where(Employee.registration == "100101456"))
    assert pj.contract_type_code == "PJ" and pj.base_salary == Decimal("15000.00")


def test_macro_assumptions(client, admin, run_worker, db):
    import_and_load(client, admin, run_worker, builders.premissas(), "premissas.xlsx")
    rows = db.scalars(select(MacroAssumption).order_by(MacroAssumption.id)).all()
    keyed = {(r.category, r.indicator, r.segment, r.year): r.value for r in rows}
    assert keyed[("MACRO", "IPCA", None, 2027)] == Decimal("0.035")
    assert keyed[("NEGOCIO", "Volume de Vendas (k M³)", "Diesel", 2027)] == Decimal("2100")
    assert ("NEGOCIO", "Volume de Vendas (k M³)", "ATEM", 2026) in keyed  # segmento com nota de fonte


def test_unrecognized_file_fails_and_can_be_rejected(client, admin, run_worker):
    batch_id = upload(client, admin, b"col1;col2\n1;2\n", "qualquer.csv")
    run_worker()
    b = status(client, admin, batch_id)
    assert b["status"] == "FAILED" and "Estrutura não reconhecida" in b["error_message"]
    assert client.post(f"/api/v1/imports/{batch_id}/confirm", headers=admin).status_code == 409
    assert client.post(f"/api/v1/imports/{batch_id}/reject", headers=admin).json()["status"] == "REJECTED"


def test_wrong_extension_rejected(client, admin):
    resp = client.post("/api/v1/imports", headers=admin, files={"file": ("a.pdf", b"%PDF", "application/pdf")})
    assert resp.status_code == 422
