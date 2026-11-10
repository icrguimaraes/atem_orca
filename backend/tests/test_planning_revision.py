"""Revisão do Planejamento: arquivo do Planejamento (nomes, sem códigos) → versão em elaboração, com prévia antes ×
depois, linhas preservadas e ajustadas na proporção, o que não veio no arquivo zerado (ou mantido) e apontado."""

import io
from decimal import Decimal

from openpyxl import Workbook
from sqlalchemy import select

from app.models import Account, AuditLog, BudgetLine, Company, CostCenter
from app.services import opex as opex_svc
from app.services.opex import context

P = "/api/v1/consolidation/planning-revision"


def _line(db, sub, cc, acc, values: dict[int, str], description: str):
    line = BudgetLine(
        submission_id=sub.id,
        company_id=cc.company_id,
        cost_center_id=cc.id,
        account_id=acc.id,
        line_type="GENERIC",
        description=description,
        justification="justificativa do gestor",
    )
    opex_svc._set_values(line, {m: Decimal(v) for m, v in values.items()})
    db.add(line)
    return line


def _setup(db):
    ctx = context(db)
    atem = db.scalar(select(Company).where(Company.code == "1001"))
    travel = Account(code="9970301011", name="Despesas com Passagens Teste", nature="OPEX")
    food = Account(code="9970301008", name="Alimentação Teste", nature="OPEX")
    interest = Account(code="9930101008", name="Juros Impost Teste", nature="FINANCEIRO")
    cc = CostCenter(company_id=atem.id, code="PR1", name="PLANEJ. TRIBUTÁRIO TESTE")
    db.add_all([travel, food, interest, cc])
    db.flush()
    sub = opex_svc.get_submission(db, ctx, cc.id)
    a = _line(db, sub, cc, travel, {1: "100.00", 2: "100.00"}, "Viagem a Belém")
    b = _line(db, sub, cc, travel, {1: "50.00"}, "Viagem a Porto Velho")
    j = _line(db, sub, cc, interest, {m: "10.00" for m in range(1, 13)}, "Juros do parcelamento")
    db.commit()
    return ctx, cc, sub, travel, food, interest, a, b, j


def _file(year: int, rows: list[tuple]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "CONSOLIDADO"
    ws.append([None])
    months = [f"01/{m:02d}/{year}" for m in range(1, 13)]
    ws.append(
        [
            "Empresa",
            "Denominação do Centro de Custos",
            "Gestor do CC",
            "Denominação da Conta do Razão",
            "Pacotes Orçamento",
            *[f"01/{m:02d}/{year - 1}" for m in range(1, 13)],
            *months,
            "Vice-presidente",
        ]
    )
    for company, cc, account, values in rows:
        ws.append([company, cc, "Gestor", account, "Viagens", *([0] * 12), *values, "VP CONTROLADORIA E TRIBUTOS"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _post(client, admin, url, content, **form):
    files = {"file": ("previa.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    return client.post(url, headers=admin, files=files, data=form)


def test_preview_and_apply_keep_lines_and_point_out_what_did_not_come(client, admin, db):
    ctx, cc, sub, travel, food, interest, a, b, j = _setup(db)
    year = ctx.target_year
    content = _file(
        year,
        [
            # nome do CC escrito por extenso no Planejamento: casa com "PLANEJ. TRIBUTÁRIO TESTE"
            ("ATEM", "PLANEJAMENTO TRIBUTÁRIO TESTE", "Despesas com Passagens Teste", [120, 100] + [0] * 10),
            ("ATEM", "PLANEJAMENTO TRIBUTÁRIO TESTE", "Alimentação Teste", [0, 0, 30] + [0] * 9),
        ],
    )
    prev = _post(client, admin, f"{P}/preview", content)
    assert prev.status_code == 200, prev.text
    data = prev.json()
    assert data["errors"] == []
    assert Decimal(data["totals"]["before"]) == Decimal("370.00")  # 250 de viagem + 120 de juros
    assert Decimal(data["totals"]["after"]) == Decimal("250.00")  # 220 de viagem + 30 de alimentação
    assert [z["account"] for z in data["zeroed"]] == [interest.code]
    assert {c["month"] for c in data["cells"] if c["account"] == travel.code} == {1}

    # prévia velha não grava
    bad = _post(client, admin, f"{P}/apply", content, digest="x", reason="Rev 2")
    assert bad.status_code == 422
    ok = _post(client, admin, f"{P}/apply", content, digest=data["digest"], reason="Retorno do Planejamento")
    assert ok.status_code == 200, ok.text
    db.expire_all()
    va = {v.month: v.amount for v in db.get(BudgetLine, a.id).values}
    vb = {v.month: v.amount for v in db.get(BudgetLine, b.id).values}
    assert va[1] + vb[1] == Decimal("120.00") and va[1] == Decimal("80.00")  # na proporção 2:1
    assert va[2] == Decimal("100.00")  # mês igual ao arquivo: não mexe
    assert db.get(BudgetLine, a.id).description == "Viagem a Belém"  # detalhe preservado
    assert sum(v.amount for v in db.get(BudgetLine, j.id).values) == 0  # juros: não veio no arquivo → zerado
    adjust = db.scalar(select(BudgetLine).where(BudgetLine.submission_id == sub.id, BudgetLine.account_id == food.id))
    assert adjust.attributes["source"] == "PLANNING_REVISION" and adjust.total_amount == Decimal("30.00")
    assert db.get(type(sub), sub.id).status == "IN_PROGRESS"
    assert db.scalar(select(AuditLog).where(AuditLog.action == "PLANNING_REVISION")) is not None

    findings = client.get("/api/v1/findings", headers=admin).json()
    items = findings.get("items") or findings.get("findings") or []
    kinds = {(i["kind"], i["subject"].split()[0]) for i in items if i["cost_center_id"] == cc.id}
    assert ("PLANNING_ZEROED", interest.code) in kinds

    # carregar de novo o mesmo arquivo não muda nada
    again = _post(client, admin, f"{P}/preview", content).json()
    assert again["changed_keys"] == 0


def test_version_name_goes_to_labels(client, admin, db):
    ctx = context(db)
    res = client.patch(f"/api/v1/consolidation/versions/{ctx.version.id}", headers=admin, json={"name": "Planejamento"})
    assert res.status_code == 200, res.text
    mine = next(v for v in res.json()["versions"] if v["id"] == ctx.version.id)
    assert mine["display_label"] == f"{ctx.version.label} · Planejamento"
    assert db.scalar(select(AuditLog).where(AuditLog.entity_type == "budget_version", AuditLog.reason.like("Nome%")))
    based = client.patch(f"/api/v1/consolidation/versions/{ctx.version.id}", headers=admin, json={"is_base": True})
    assert [v["is_base"] for v in based.json()["versions"]].count(True) == 1
    assert next(v for v in based.json()["versions"] if v["id"] == ctx.version.id)["name"] == "Planejamento"


def test_absent_keep_and_unmatched_names_block(client, admin, db):
    ctx, cc, sub, travel, food, interest, a, b, j = _setup(db)
    year = ctx.target_year
    content = _file(
        year, [("ATEM", "PLANEJAMENTO TRIBUTÁRIO TESTE", "Despesas com Passagens Teste", [150, 100] + [0] * 10)]
    )
    data = _post(client, admin, f"{P}/preview", content, absent="keep").json()
    assert data["zeroed"] == [] and [k["account"] for k in data["kept_absent"]] == [interest.code]
    assert data["changed_keys"] == 0  # passagens iguais; juros mantidos

    # dois CCs parecidos: vale o que casa mais palavras; nome repetido: vale o que já tem orçamento
    atem = db.scalar(select(Company).where(Company.code == "1001"))
    db.add_all(
        [
            CostCenter(company_id=atem.id, code="PR9", name="PLANEJ. TRIBUTÁRIO TESTE"),
            CostCenter(company_id=atem.id, code="PRX1", name="CONTROLADORIA TESTX"),
            CostCenter(company_id=atem.id, code="PRX2", name="VP DE CONTROLADORIA TESTX"),
        ]
    )
    db.commit()
    vp = _file(year, [("ATEM", "VP CONTROLADORIA E TRIBUTOS TESTX", "Alimentação Teste", [5] + [0] * 11)])
    res = _post(client, admin, f"{P}/preview", vp).json()
    assert res["errors"] == [] and any(m["code"] == "PRX2" for m in res["matched_cost_centers"])
    again = _post(client, admin, f"{P}/preview", content).json()
    assert again["errors"] == [] and [m["code"] for m in again["matched_cost_centers"]] == ["PR1"]

    wrong = _file(year, [("ATEM", "CC QUE NAO EXISTE", "Despesas com Passagens Teste", [1] + [0] * 11)])
    res = _post(client, admin, f"{P}/preview", wrong).json()
    assert res["errors"] and "nenhum CC" in res["errors"][0]["message"]
    blocked = _post(client, admin, f"{P}/apply", wrong, digest=res["digest"], reason="x")
    assert blocked.status_code == 422
