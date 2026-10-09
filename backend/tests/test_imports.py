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
        ),  # promoção sem mês/novo salário → entra com pendências (aviso), não erro
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
    assert (b["total_rows"], b["valid_rows"], b["error_rows"]) == (5, 4, 1)
    client.post(f"/api/v1/imports/{batch_id}/confirm", headers=admin)
    run_worker()
    final = status(client, admin, batch_id)
    assert final["summary"]["load"]["employees_created"] == 3
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


def test_projection_fills_only_months_without_ksb1(client, admin, run_worker):
    """Projeção do gestor: entra só nos meses sem KSB1 (out–dez), é marcada como projetada, substitui a anterior
    e não altera o realizado de jan–set. Com projeção, o ano passa a valer completo."""
    _seed_cc(client, admin, run_worker)
    lines = [
        (
            "1001",
            "1050101011",
            "6010301001",
            "Hospedagem",
            "2026",
            "3",
            "2026-03-15",
            "1",
            "100",
            "BRL",
            "t",
            None,
            None,
        ),
        (
            "1001",
            "1050101011",
            "6010301001",
            "Hospedagem",
            "2026",
            "9",
            "2026-09-15",
            "2",
            "200",
            "BRL",
            "t",
            None,
            None,
        ),
    ]
    import_and_load(client, admin, run_worker, builders.ksb1_csv(lines), "ksb1_2026_parcial.csv", dataset_type="ACTUAL")
    before = client.get("/api/v1/dashboard/overview?years=2026&compare=false", headers=admin).json()["kpis"]["ref_ytd"]
    proj = [
        ("1050101011", 10, "6010301001", "Hospedagem", 50),
        ("1050101011", 11, "6010301001", "Hospedagem", 70),
        ("1050101011", 9, "6010301001", "Hospedagem", 999),
    ]
    import_and_load(
        client, admin, run_worker, builders.projecao_base(proj), "PROJETADO 2026.xlsx", dataset_type="PROJECTION"
    )
    after = client.get("/api/v1/dashboard/overview?years=2026&compare=false", headers=admin).json()
    # set (9) do arquivo é ignorado: o KSB1 é o realizado até setembro; out e nov entram como projeção
    assert float(after["kpis"]["ref_ytd"]) == float(before) + 120
    proj_months = sorted(round(float(r["proj"])) for r in after["monthly"] if float(r["proj"]) > 0)
    assert proj_months == [50, 70]
    # os gráficos (figures=true) também precisam montar com a série da projeção
    figs = client.get("/api/v1/dashboard/overview?years=2026&compare=false&figures=true", headers=admin)
    assert figs.status_code == 200, figs.text
    for extra in ("&compare=true", "&modules=PERSONNEL", "&modules=OPEX&months=10,11", "&same_period=false"):
        again_figs = client.get(f"/api/v1/dashboard/overview?years=2026&figures=true{extra}", headers=admin)
        assert again_figs.status_code == 200, (extra, again_figs.text[:200])
    names = [t["name"] for t in figs.json()["figures"]["monthly"]["data"]]
    assert any("Projeção" in n for n in names), names
    # a projeção vem logo depois do realizado, na mesma coluna (antes da barra do orçamento); as outras séries
    # têm coluna própria (sem offsetgroup, o Plotly sobrepunha o orçamento ao realizado)
    groups = {t["name"]: t.get("offsetgroup") for t in figs.json()["figures"]["monthly"]["data"]}
    proj_name = next(n for n in names if "Projeção" in n)
    real_name = next(n for n in names if "Realizado" in n)
    assert groups[proj_name] == groups[real_name] and all(groups.values())
    others = [g for n, g in groups.items() if n != proj_name]
    assert len(others) == len(set(others)), groups
    assert (
        names.index(next(n for n in names if "Projeção" in n))
        == names.index(next(n for n in names if "Realizado" in n)) + 1
    )
    # ano do ciclo no filtro: orçamento 2027 é a série principal e o realizado 2026 (completo) é a base
    both = client.get("/api/v1/dashboard/overview?years=2026,2027&figures=true", headers=admin)
    assert both.status_code == 200, both.text[:200]
    period = both.json()["period"]
    assert period["main"] == "budget" and period["base_kind"] == "actual", period
    # maiores CCs: total de todos os CCs no cabeçalho e rótulo também na barra da base
    total = both.json()["top_cost_centers_total"]
    assert total["base"] == both.json()["kpis"]["prev_ytd"], total
    from app.services import painel_figures

    lb = {"main": "Orçamento 2027", "base": "Realizado 2026", "main_color": "#000", "base_color": "#111"}
    rank = painel_figures.fig_rank([{"id": 1, "name": "CC", "value": "10", "base": "8", "var_pct": "0.25"}], lb, True)
    assert rank["data"][1]["text"] == ["<b>R$ 8</b>"], rank["data"][1]["text"]
    assert painel_figures.fig_rank_total({"main": "10", "base": "8", "var_pct": "0.25"}, lb) is not None
    # projeção até nov (11 meses): a base sai anualizada por 12/11
    assert round(float(both.json()["kpis"]["prev_ytd"]), 2) == round(float(after["kpis"]["ref_ytd"]) * 12 / 11, 2)
    # projeção substitui a anterior inteira (não soma)
    import_and_load(
        client,
        admin,
        run_worker,
        builders.projecao_base([("1050101011", 12, "6010301001", "Hospedagem", 30)]),
        "PROJETADO v2.xlsx",
        dataset_type="PROJECTION",
    )
    again = client.get("/api/v1/dashboard/overview?years=2026&compare=false", headers=admin).json()["kpis"]
    assert float(again["ref_ytd"]) == float(before) + 30


def test_projection_cc_without_code_maps_csc_with_warning(client, admin, run_worker):
    """Linha da projeção sem código de CC e com a descrição 'CSC' é associada ao CC 1050101012, com aviso na prévia."""
    _seed_cc(client, admin, run_worker)
    client.post(
        "/api/v1/cost-centers",
        headers=admin,
        json={
            "company_id": client.get("/api/v1/companies", headers=admin).json()[0]["id"],
            "code": "1050101012",
            "name": "CSC",
        },
    )
    rows = [(None, 10, "6010301001", "Hospedagem", 25500)]
    batch_id = upload(client, admin, builders.projecao_base(rows), "PROJETADO csc.xlsx", dataset_type="PROJECTION")
    run_worker()
    b = status(client, admin, batch_id)
    assert b["status"] == "VALIDATED" and b["valid_rows"] == 1 and b["warning_rows"] == 1, b


def test_capex_acum_actual_and_projection(client, admin, run_worker):
    """CAPEX acumulado: realizado jan–set entra como Realizado (sem tocar o KSB1) e a projeção out–dez entra como
    Projeção sem apagar a do OPEX; dez/2025 e meses futuros do realizado ficam de fora."""
    _seed_cc(client, admin, run_worker)
    lines = [
        (
            "1001",
            "1050101011",
            "6010301001",
            "Hospedagem",
            "2026",
            "3",
            "2026-03-15",
            "1",
            "100",
            "BRL",
            "t",
            None,
            None,
        ),
        (
            "1001",
            "1050101011",
            "6010301001",
            "Hospedagem",
            "2026",
            "9",
            "2026-09-15",
            "2",
            "200",
            "BRL",
            "t",
            None,
            None,
        ),
    ]
    import_and_load(client, admin, run_worker, builders.ksb1_csv(lines), "ksb1.csv", dataset_type="ACTUAL")
    import_and_load(
        client,
        admin,
        run_worker,
        builders.projecao_base([("1050101011", 10, "6010301001", "Hospedagem", 40)]),
        "proj.xlsx",
        dataset_type="PROJECTION",
    )
    base = float(
        client.get("/api/v1/dashboard/overview?years=2026&compare=false", headers=admin).json()["kpis"]["ref_ytd"]
    )
    assert base == 340
    rows = [
        (2025, 12, "1050101011", "1020601005", 900, 900),  # dez/2025: fora
        (2026, 4, "1050101011", "1020601005", 50, 50),
        (2026, 9, "1050101011", "1020601005", 5, 77),
        (2026, 11, "1050101011", "1020601005", 7, 60),  # realizado de mês futuro: fora; projeção 60 entra
    ]
    import_and_load(client, admin, run_worker, builders.capex_acum(rows), "Capex Acum.xlsx", dataset_type="ACTUAL")
    after_actual = float(
        client.get("/api/v1/dashboard/overview?years=2026&compare=false", headers=admin).json()["kpis"]["ref_ytd"]
    )
    assert after_actual == base + 55  # 50 (abr) + 5 (set); KSB1 e projeção do OPEX intactos
    import_and_load(
        client,
        admin,
        run_worker,
        builders.capex_acum(rows),
        "Capex Acum proj.xlsx",
        force=True,
        dataset_type="PROJECTION",
    )
    final = client.get("/api/v1/dashboard/overview?years=2026&compare=false&figures=true", headers=admin)
    assert final.status_code == 200, final.text
    assert float(final.json()["kpis"]["ref_ytd"]) == base + 55 + 60  # projeção do CAPEX soma sem apagar a do OPEX (40)


def test_wide_with_budget_and_actual_blocks_uses_the_right_months():
    """Acompanhamento orçamentário (ex.: 'Realizado 2025'): meses repetidos no cabeçalho, com ORÇADO/REAL na linha
    de cima (e uma linha de totais no meio). Realizado lê o bloco REAL; orçado de referência, o bloco ORÇADO."""
    import io
    from datetime import datetime

    from openpyxl import Workbook

    from app.imports.base import load_sheets
    from app.imports.parsers.financial import parse_wide

    wb = Workbook()
    ws = wb.active
    ws.title = "ANÁLISE"
    months = [datetime(2025, m, 1) for m in range(1, 13)]
    ws.append([None] * 5 + ["ORÇADO"] * 12 + ["REAL"] * 12)
    ws.append([None] * 5 + [1] * 24)  # totais
    ws.append(["Empresa", "Centro de Custos", "Conta Razão", "Pacotes Orçamento", "Gestor", *months, *months])
    ws.append([1001, 1050101001, 6010301001, "Viagens", "X", *([100] * 12), *([7] * 12)])
    buf = io.BytesIO()
    wb.save(buf)
    sheets = load_sheets(buf.getvalue(), "Realizado 2025.xlsx")
    actual = parse_wide(sheets, {}, "ACTUAL")
    assert actual.meta["block"] == "REAL" and actual.meta["year"] == 2025
    assert actual.records[0].data["values"] == {m: "7" for m in range(1, 13)}
    budget = parse_wide(sheets, {}, "REFERENCE_BUDGET")
    assert budget.meta["block"] == "ORCADO" and budget.records[0].data["values"][1] == "100"


def test_reference_budget_in_rows_reads_only_the_budget_tab():
    """Base de controle da Controladoria: abas 'Realizado 2026' e 'Orçamento 2026' no mesmo layout em linhas
    (CC × conta × mês). O orçado de referência lê só a coluna 'Orçamento 2026'; linhas repetidas somam, a linha de
    total geral fica de fora e o CC com prefixo de filial é lido pelo código SAP, com aviso."""
    import io
    from datetime import datetime

    from openpyxl import Workbook

    from app.imports.base import load_sheets
    from app.imports.detector import detect_and_parse

    header = ["Cód CC_Código conta", "Mês", "Empresa", "Tp", "Centro de Custo SAP", "Cód CC", "Descrição C. Custo"]
    header += ["Centro financ./item orçamento", "Código conta", "Descrição Conta Contábil", "Pacote"]
    jan, fev = datetime(2026, 1, 1), datetime(2026, 2, 1)

    def line(month, company, cc, account, value):
        return [f"{cc}{account}", month, company, "X", f"* 01-{cc}", cc, "CC", account, account, "Conta", "P", value]

    wb = Workbook()
    real = wb.active
    real.title = "Realizado 2026"
    real.append([*header, "Total 3ª Projeção"])
    real.append(line(jan, 1001, 1050101002, 6010301005, 999))
    budget = wb.create_sheet("Orçamento 2026")
    budget.append([*header, "Orçamento 2026"])
    budget.append(line(jan, 1001, 1050101002, 6010301005, 100.5))
    budget.append(line(jan, 1001, 1050101002, 6010301005, 20))  # mesma chave: soma
    budget.append(line(fev, 1001, 1050101002, 6010301005, -30))  # negativo: entra (aviso no resolver)
    budget.append(line(jan, 1001, 1050101002, 6010301006, 0))  # zerado: fora
    budget.append(line(jan, 1001, "03-1050101003", 6020101010, 12.34))
    budget.append(line(jan, 2001, "RFM6001000", 6020201005, 500))
    budget.append([None] * 11 + [602.84])  # total geral
    buf = io.BytesIO()
    wb.save(buf)
    sheets = load_sheets(buf.getvalue(), "Base controle orçamentário controladoria.xlsx")

    result = detect_and_parse(sheets, "REFERENCE_BUDGET", {})
    assert result.layout == "BUDGET_LONG" and result.meta["sheet"] == "Orçamento 2026"
    by_key = {
        (r.data["company"], r.data["cost_center"], r.data["account"], *r.data["values"]): r for r in result.records
    }
    assert by_key[("1001", "1050101002", "6010301005", 1)].data["values"] == {1: "120.5"}
    assert by_key[("1001", "1050101002", "6010301005", 2)].data["values"] == {2: "-30"}
    fiscal = by_key[("1001", "1050101003", "6020101010", 1)]
    assert [i.code for i in fiscal.issues] == ["CC_NORMALIZED"] and fiscal.data["year"] == 2026
    assert by_key[("2001", "RFM6001000", "6020201005", 1)].data["values"] == {1: "500"}
    assert len(result.records) == 4
    total = sum(Decimal(v) for r in result.records for v in r.data["values"].values())
    assert total == Decimal("602.84")  # bate com a linha de total da planilha (o realizado não entra)
    other = detect_and_parse(sheets, "REFERENCE_BUDGET", {"reference_year": 2025})
    assert other.records == [] and other.structural[0].code == "OTHER_YEAR_IGNORED"


def test_actual_long_replaces_opex_and_personnel_keeps_capex(client, admin, run_worker):
    """Realizado em linhas (aba 'Realizado 2026' da base de controle, 'Total 3ª Projeção'): os meses até o corte do
    realizado entram como realizado e substituem OPEX/Pessoal (o KSB1); o CAPEX já carregado continua; meses depois do
    corte ficam para a projeção; CC escrito com prefixo é lido pelo código, com aviso."""
    import io

    from openpyxl import load_workbook

    _seed_cc(client, admin, run_worker)
    ksb1 = [
        ("1001", "1050101011", "6010301001", "Hospedagem", "2026", str(m), f"2026-0{m}-15", "1", str(v), "BRL", "t")
        + (None, None)
        for m, v in ((3, 100), (9, 200))
    ]
    import_and_load(client, admin, run_worker, builders.ksb1_csv(ksb1), "ksb1.csv", dataset_type="ACTUAL")
    capex = [(2026, 4, "1050101011", "1020601005", 50, 50), (2026, 9, "1050101011", "1020601005", 5, 77)]
    import_and_load(client, admin, run_worker, builders.capex_acum(capex), "Capex Acum.xlsx", dataset_type="ACTUAL")

    def total():
        url = "/api/v1/dashboard/overview?years=2026&compare=false"
        return float(client.get(url, headers=admin).json()["kpis"]["ref_ytd"])

    assert total() == 355  # 300 do KSB1 + 55 de CAPEX
    rows = [
        ("1050101011", 3, "6010301001", "Hospedagem", 130),
        ("1050101011", 9, "6010301001", "Hospedagem", 170),
        ("03-1050101011", 5, "6010301001", "Hospedagem", 10),
        ("1050101011", 11, "6010301001", "Hospedagem", 999),  # mês projetado: não entra como realizado
    ]
    wb = load_workbook(io.BytesIO(builders.projecao_base(rows)))
    wb.active.title = "Realizado 2026"
    buf = io.BytesIO()
    wb.save(buf)
    final = import_and_load(client, admin, run_worker, buf.getvalue(), "Base controle.xlsx", dataset_type="ACTUAL")
    assert final["status"] == "COMPLETED", final
    assert total() == 365  # 130 + 170 + 10 do arquivo + 55 de CAPEX mantido
