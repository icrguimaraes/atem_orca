from decimal import Decimal

from tests import builders
from tests.test_imports import import_and_load, status, upload
from tests.test_opex import _user

CC1, CC2 = "1050101011", "1050101012"
QUADRO = [
    # matrícula, nome, cargo, empresa, divisão, CC, salário, ação, mês, novo cargo, novo salário, contrato, creche, VT
    ("100", "ANA", "ANALISTA", "1001", "0001", CC1, 10000, "MANTER", None, None, None, "CLT", "NÃO", "SIM"),
    ("200", "BRUNO", "ANALISTA", "1001", "0001", CC1, 8000, "PROMOVER", 7, "COORDENADOR", 9000, "CLT", "SIM", "SIM"),
    ("300", "CARLA", "CONSULTORA", "1001", "0001", CC1, 5000, "REMOVER", 4, None, None, "PJ", None, None),
    (
        None,
        "VAGA ANALISTA DE DADOS",
        "ANALISTA",
        "1001",
        "0001",
        CC1,
        6000,
        "INCLUIR",
        10,
        None,
        None,
        "CLT",
        None,
        None,
    ),
]


def _setup(client, admin, run_worker):
    import_and_load(client, admin, run_worker, builders.opex_template_bd(), "bd.xlsx", dataset_type="MASTER_DATA")
    final = import_and_load(client, admin, run_worker, builders.quadro_funcionarios(QUADRO), "quadro.xlsx")
    assert final["status"] == "COMPLETED", final
    mgr_id, mgr = _user(client, admin, "gestor@t.com", ["MANAGER"])
    ccs = {c["code"]: c for c in client.get("/api/v1/cost-centers", headers=admin).json()}
    client.patch(f"/api/v1/cost-centers/{ccs[CC1]['id']}", headers=admin, json={"manager_user_id": mgr_id})
    cycle_id = client.get("/api/v1/cycles", headers=admin).json()[0]["id"]
    client.post(f"/api/v1/cycles/{cycle_id}/open", headers=admin)
    # valores exatos sem o abono anual do CLT (ele tem teste próprio em test_personnel)
    client.put(f"/api/v1/cycles/{cycle_id}/parameters/personnel.annual_bonus_clt", headers=admin, json={"value": 0})
    return ccs, mgr, final


def test_annual_bonus_clt_in_gratification_account(client, admin, run_worker):
    """Abono anual do CLT (09/10/2026): R$ 2.500 por colaborador no ano, 1/12 por mês ativo, sem multiplicador,
    na conta de gratificação (6010101009); PJ não recebe."""
    ccs, _mgr, _final = _setup(client, admin, run_worker)
    head = client.get(f"/api/v1/personnel/cost-centers/{ccs[CC1]['id']}", headers=admin).json()
    url = f"/api/v1/personnel/submissions/{head['submission_id']}/view"
    before = client.get(url, headers=admin).json()
    cycle_id = client.get("/api/v1/cycles", headers=admin).json()[0]["id"]
    r = client.put(
        f"/api/v1/cycles/{cycle_id}/parameters/personnel.annual_bonus_clt", headers=admin, json={"value": 2500}
    )
    assert r.status_code == 200, r.text
    after = client.get(url, headers=admin).json()
    clt_months = sum(sum(p["headcount"]) for p in after["positions"] if p["contract_type_code"] == "CLT")
    expected = Decimal(2500) * clt_months / 12
    assert abs(Decimal(after["totals"]["bonus_total"]) - expected) < Decimal("0.05"), after["totals"]["bonus_total"]
    diff = Decimal(after["totals"]["annual"]) - Decimal(before["totals"]["annual"])
    assert diff == Decimal(after["totals"]["bonus_total"])  # o abono não passa pelo multiplicador
    assert after["totals"]["charges_total"] == before["totals"]["charges_total"]
    pj = [p for p in after["positions"] if p["contract_type_code"] == "PJ"]
    for p in pj:
        old = next(q for q in before["positions"] if q["key"] == p["key"])
        assert p["annual"] == old["annual"]
    rows = client.get("/api/v1/consolidation/overview", headers=admin).json()
    assert "6010101009" in str(rows)


def _by_name(view):
    return {p["name"]: p for p in view["positions"]}


def test_personnel_projection_workflow_and_transfer(client, admin, run_worker):
    ccs, mgr, final = _setup(client, admin, run_worker)
    assert final["summary"]["load"]["movements"] == {
        "promotion": 1,
        "termination": 1,
        "hires": 1,
        "cost_centers": 1,
    }

    head = client.get(f"/api/v1/personnel/cost-centers/{ccs[CC1]['id']}", headers=mgr).json()
    sub = head["submission_id"]
    assert head["status"] == "IN_PROGRESS" and head["permissions"]["edit"]
    view = client.get(f"/api/v1/personnel/submissions/{sub}/view", headers=mgr).json()
    rows = _by_name(view)
    # reajuste 5% desde JAN; CLT × 1,8; PJ sem multiplicador
    assert rows["ANA"]["annual"] == "226800.00"
    assert rows["BRUNO"]["annual"] == "192780.00" and rows["BRUNO"]["movement"]["new_position"] == "COORDENADOR"
    assert rows["CARLA"]["annual"] == "15750.00" and rows["CARLA"]["headcount"][3] == 0
    vaga = next(p for p in view["positions"] if p["kind"] == "HIRE")
    assert vaga["annual"] == "34020.00" and vaga["monthly"][8] == "0.00" and vaga["monthly"][9] == "11340.00"
    t = view["totals"]
    assert t["annual"] == "469350.00" and t["headcount_start"] == 3 and t["headcount_end"] == 3
    assert (t["hires"], t["terminations"], t["promotions"]) == (1, 1, 1)
    assert Decimal(t["salary_total"]) + Decimal(t["charges_total"]) + Decimal(t["severance_total"]) == Decimal(
        "469350.00"
    )
    assert {b["code"] for b in view["benefits"]} == {"AUX_CRECHE", "VALE_TRANSPORTE"}

    # desligamento e contratação exigem justificativa para enviar quando o ciclo bloqueia por justificativa
    cycle_id = client.get("/api/v1/cycles", headers=admin).json()[0]["id"]
    client.put(f"/api/v1/cycles/{cycle_id}/parameters/review.justification_blocks", headers=admin, json={"value": True})
    resp = client.post(f"/api/v1/personnel/submissions/{sub}/actions/submit", headers=mgr, json={})
    assert resp.status_code == 409 and "justificativa" in resp.json()["detail"]
    client.put(
        f"/api/v1/cycles/{cycle_id}/parameters/review.justification_blocks", headers=admin, json={"value": False}
    )
    carla = rows["CARLA"]["employee_id"]
    v = client.put(
        f"/api/v1/personnel/submissions/{sub}/employees/{carla}/movement",
        headers=mgr,
        json={"type": "TERMINATION", "month": 4, "severance_cost": 3000, "reason": "Fim do contrato"},
    ).json()
    assert _by_name(v)["CARLA"]["annual"] == "18750.00"  # + verba rescisória em ABR
    client.patch(
        f"/api/v1/personnel/movements/{vaga['movement']['id']}",
        headers=mgr,
        json={
            "position_name": "ANALISTA DE DADOS",
            "quantity": 2,
            "month": 10,
            "new_salary": 6000,
            "reason": "Expansão do time",
        },
    )

    # transferência: Ana sai do CC1 em JUL e entra no CC2
    ana = rows["ANA"]["employee_id"]
    bad = client.put(
        f"/api/v1/personnel/submissions/{sub}/employees/{ana}/movement",
        headers=mgr,
        json={"type": "TRANSFER", "month": 7, "target_cost_center_id": ccs[CC1]["id"]},
    )
    assert bad.status_code == 422
    v = client.put(  # transferência entre CCs de gestores diferentes: feita pela Controladoria
        f"/api/v1/personnel/submissions/{sub}/employees/{ana}/movement",
        headers=admin,
        json={"type": "TRANSFER", "month": 7, "target_cost_center_id": ccs[CC2]["id"], "reason": "Nova área"},
    ).json()
    assert _by_name(v)["ANA"]["annual"] == "113400.00"
    head2 = client.get(f"/api/v1/personnel/cost-centers/{ccs[CC2]['id']}", headers=admin).json()
    v2 = client.get(f"/api/v1/personnel/submissions/{head2['submission_id']}/view", headers=admin).json()
    incoming = v2["positions"][0]
    assert incoming["kind"] == "TRANSFER_IN" and incoming["annual"] == "113400.00"
    assert incoming["from_cost_center"].startswith(CC1)

    # "justificar tudo": promoções e reajustes também pedem justificativa — preenchidas na tela de Justificativas
    items = client.get(f"/api/v1/justifications?cost_center_id={ccs[CC1]['id']}", headers=mgr).json()["items"]
    missing = [i for i in items if i["module"] == "PERSONNEL" and not i["justified"]]
    assert missing and all(i["editable"] for i in missing)
    for i in missing:
        r = client.put("/api/v1/justifications", headers=mgr, json={"key": i["key"], "text": "Plano de carreira 2027"})
        assert r.status_code == 200 and r.json()["justified"], r.text
    logs = client.get("/api/v1/findings", headers=admin).json()["reviews"]
    assert any(r["note"] == "Justificativa: Plano de carreira 2027" for r in logs)
    xlsx = client.get("/api/v1/justifications/export.xlsx", headers=admin)
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"

    head = client.post(f"/api/v1/personnel/submissions/{sub}/actions/submit", headers=mgr, json={}).json()
    assert head["status"] == "SUBMITTED" and head["package_review"]["status"] == "PENDING"
    client.post(f"/api/v1/personnel/submissions/{sub}/actions/start_review", headers=admin, json={})
    resp = client.post(f"/api/v1/personnel/submissions/{sub}/actions/approve", headers=admin, json={})
    assert resp.status_code == 409 and "Pessoas" in resp.json()["detail"]
    client.post(f"/api/v1/personnel/submissions/{sub}/package-review", headers=admin, json={"status": "APPROVED"})
    head = client.post(f"/api/v1/personnel/submissions/{sub}/actions/approve", headers=admin, json={}).json()
    assert head["status"] == "APPROVED"
    assert (
        client.put(
            f"/api/v1/personnel/submissions/{sub}/employees/{ana}/movement", headers=mgr, json={"type": "KEEP"}
        ).status_code
        == 409
    )

    summary = client.get("/api/v1/personnel/summary", headers=admin).json()
    row = next(r for r in summary["rows"] if r["code"] == CC1)
    assert row["status"] == "APPROVED" and row["hires"] == 2 and row["terminations"] == 1
    assert summary["hires_by_month"][9] == 2 and summary["terminations_by_month"][3] == 1
    # gestor sem vínculo não vê o CC
    _, other = _user(client, admin, "outro@t.com", ["MANAGER"])
    assert client.get(f"/api/v1/personnel/submissions/{sub}/view", headers=other).status_code == 403


def test_what_if_and_scenarios(client, admin, run_worker):
    ccs, mgr, _ = _setup(client, admin, run_worker)
    r = client.post("/api/v1/personnel/what-if", headers=admin, json={"multipliers": {"CLT": 2.0}}).json()
    assert r["base"]["annual"] == "469350.00"
    # CLT: (226800 + 192780 + 34020) / 1,8 × 2,0 = 504000; PJ inalterado
    assert r["simulation"]["annual"] == "519750.00"
    pj = next(c for c in r["by_contract"] if c["contract"] == "PJ")
    assert pj["difference"] == "0.00"
    clt = next(c for c in r["by_contract"] if c["contract"] == "CLT")
    assert Decimal(clt["simulated"]) == Decimal("504000.00") and r["difference"] == "50400.00"
    assert "PJ" in r["simulated"]["ignored_multiplier_for"]

    # gestor não cria cenário; controladoria cria e torna base
    assert client.post("/api/v1/personnel/scenarios", headers=mgr, json={"name": "x"}).status_code == 403
    sc = client.post(
        "/api/v1/personnel/scenarios", headers=admin, json={"name": "Reajuste 7%", "salary_adjustment_pct": 0.07}
    ).json()
    assert sc["multipliers"]["CLT"] == "1.800000" and not sc["is_baseline"]
    client.post(f"/api/v1/personnel/scenarios/{sc['id']}/baseline", headers=admin)
    names = {s["name"]: s for s in client.get("/api/v1/personnel/scenarios", headers=admin).json()}
    assert names["Reajuste 7%"]["is_baseline"] and not names["Base"]["is_baseline"]
    assert client.delete(f"/api/v1/personnel/scenarios/{sc['id']}", headers=admin).status_code == 409
    head = client.get(f"/api/v1/personnel/cost-centers/{ccs[CC1]['id']}", headers=admin).json()
    view = client.get(f"/api/v1/personnel/submissions/{head['submission_id']}/view", headers=admin).json()
    assert _by_name(view)["ANA"]["annual"] == "231120.00"  # 10000 × 1,07 × 1,8 × 12


def test_quadro_without_cost_center_uses_default(client, admin, run_worker):
    import_and_load(client, admin, run_worker, builders.opex_template_bd(), "bd.xlsx", dataset_type="MASTER_DATA")
    rows = [r[:5] + (None,) + r[6:] for r in QUADRO]
    batch_id = upload(client, admin, builders.quadro_funcionarios(rows), "q.xlsx", cost_center_code=CC2)
    run_worker()
    b = status(client, admin, batch_id)
    assert b["summary"]["comparison"]["movements"][0]["cost_center"] == CC2
    assert b["summary"]["comparison"]["movements"][0]["actions"] == 2


def test_quadro_pending_cost_center_and_salary(client, admin, run_worker):
    """Planilha com CC vazio e promoção sem novo salário entra com pendências: CC pelo cargo (setor) e salário
    a informar em Apontamentos; quem não tem setor com CC fica sem CC (aviso)."""
    import_and_load(client, admin, run_worker, builders.opex_template_bd(), "bd.xlsx", dataset_type="MASTER_DATA")
    ccs = {c["code"]: c for c in client.get("/api/v1/cost-centers", headers=admin).json()}
    contab = next(a for a in client.get("/api/v1/areas", headers=admin).json() if a["name"] == "Contabilidade")
    client.patch(
        f"/api/v1/cost-centers/{ccs[CC1]['id']}",
        headers=admin,
        json={"area_id": contab["id"], "department_id": contab["department_id"]},
    )
    rows = [
        (
            "400",
            "DIANA",
            "ANALISTA CONTABIL JR",
            None,
            None,
            None,
            7000,
            "PROMOVER",
            5,
            "ANALISTA CONTABIL PL",
            None,
            "CLT",
            None,
            None,
        ),
        ("500", "EDU", "ANALISTA CONTABIL SR", None, None, None, 9000, "MANTER", None, None, None, "CLT", None, None),
        ("600", "FABIO", "ANALISTA DE CSC SR", None, None, None, 8000, "MANTER", None, None, None, "CLT", None, None),
    ]
    batch_id = upload(client, admin, builders.quadro_funcionarios(rows), "q.xlsx")
    run_worker()
    errors = client.get(f"/api/v1/imports/{batch_id}/errors", headers=admin).json()
    codes = {e["code"] for e in (errors["items"] if isinstance(errors, dict) else errors)}
    assert {"CC_FROM_POSITION", "PROMOTION_NO_SALARY", "NO_COST_CENTER"} <= codes
    assert status(client, admin, batch_id)["error_rows"] == 0
    assert client.post(f"/api/v1/imports/{batch_id}/confirm", headers=admin).status_code == 200
    run_worker()
    assert status(client, admin, batch_id)["status"] == "COMPLETED"

    found = [i for i in client.get("/api/v1/findings", headers=admin).json()["items"] if i["module"] == "PERSONNEL"]
    salary = [i for i in found if i["kind"] == "PERSONNEL_NO_SALARY"]
    guessed = [i for i in found if i["kind"] == "PERSONNEL_CC_GUESSED"]
    assert [i["subject"] for i in salary] == ["Promoção de DIANA"] and salary[0]["severity"] == "CRITICAL"
    assert sorted(i["subject"] for i in guessed) == ["DIANA · ANALISTA CONTABIL JR", "EDU · ANALISTA CONTABIL SR"]
    assert all(i["cost_center_id"] == ccs[CC1]["id"] for i in salary + guessed)
    # a projeção funciona com a promoção pendente (sem aumento) e o envio fica bloqueado até informar o salário
    head = client.get(f"/api/v1/personnel/cost-centers/{ccs[CC1]['id']}", headers=admin).json()
    assert client.get(f"/api/v1/personnel/submissions/{head['submission_id']}/view", headers=admin).status_code == 200
    sent = client.post(f"/api/v1/personnel/submissions/{head['submission_id']}/actions/submit", headers=admin, json={})
    assert sent.status_code == 409 and "novo salário pendente" in sent.json()["detail"]

    fixed = client.post("/api/v1/findings/fix", headers=admin, json={"key": salary[0]["key"], "amount": 7800})
    assert fixed.status_code == 200 and fixed.json()["note"] == "Novo salário: R$ 7.800,00"
    edu = next(i for i in guessed if i["subject"].startswith("EDU"))
    confirmed = client.post("/api/v1/findings/fix", headers=admin, json={"key": edu["key"]})
    assert confirmed.status_code == 200 and confirmed.json()["note"].startswith(f"Centro de custo confirmado: {CC1}")
    left = {i["key"] for i in client.get("/api/v1/findings", headers=admin).json()["items"]}
    assert salary[0]["key"] not in left and edu["key"] not in left


def test_quadro_in_two_files_for_same_cost_center(client, admin, run_worker):
    """O quadro de um CC chega em dois arquivos: o segundo não apaga as promoções do primeiro; reimportar um arquivo
    substitui só as movimentações das pessoas dele."""
    ccs, _, _ = _setup(client, admin, run_worker)  # CC1: promoção (BRUNO), desligamento (CARLA) e uma vaga
    other = [
        ("700", "GIL", "ANALISTA", "1001", "0001", CC1, 6000, "PROMOVER", 3, None, 6600, "CLT", None, None),
        ("800", "HELO", "ANALISTA", "1001", "0001", CC1, 6500, "MANTER", None, None, None, "CLT", None, None),
    ]
    second = import_and_load(client, admin, run_worker, builders.quadro_funcionarios(other), "q2.xlsx")
    assert second["summary"]["load"]["movements"].get("replaced", 0) == 0
    head = client.get(f"/api/v1/personnel/cost-centers/{ccs[CC1]['id']}", headers=admin).json()
    view = client.get(f"/api/v1/personnel/submissions/{head['submission_id']}/view", headers=admin).json()
    moved = {p["name"]: (p.get("movement") or {}).get("type") for p in view["positions"] if p.get("movement")}
    assert moved.get("BRUNO") == "PROMOTION" and moved.get("CARLA") == "TERMINATION" and moved.get("GIL") == "PROMOTION"
    # reimportar o segundo arquivo substitui só a promoção do GIL (as do primeiro ficam)
    again = import_and_load(client, admin, run_worker, builders.quadro_funcionarios(other), "q2b.xlsx", force=True)
    assert again["summary"]["load"]["movements"]["replaced"] == 1
    view = client.get(f"/api/v1/personnel/submissions/{head['submission_id']}/view", headers=admin).json()
    moved = {p["name"]: (p.get("movement") or {}).get("type") for p in view["positions"] if p.get("movement")}
    assert moved.get("BRUNO") == "PROMOTION" and moved.get("GIL") == "PROMOTION"


def test_quadro_action_without_month_and_merit(client, admin, run_worker):
    """REMOVER sem mês e ação "MÉRITO" (fora da lista) entram com pendência em vez de ficar fora do quadro: o
    desligamento não mexe no custo até informar o mês; o mérito vira reajuste individual sem novo salário."""
    ccs, _, _ = _setup(client, admin, run_worker)
    rows = [
        ("700", "GIL", "ANALISTA", "1001", "0001", CC1, 6000, "REMOVER", None, None, None, "CLT", None, None),
        ("800", "HELO", "ANALISTA", "1001", "0001", CC1, 6500, "MÉRITO", 6, None, None, "CLT", None, None),
    ]
    batch_id = upload(client, admin, builders.quadro_funcionarios(rows), "q3.xlsx")
    run_worker()
    errors = client.get(f"/api/v1/imports/{batch_id}/errors", headers=admin).json()
    codes = {e["code"] for e in (errors["items"] if isinstance(errors, dict) else errors)}
    assert {"ACTION_NO_MONTH", "ACTION_ALIAS", "ADJUSTMENT_NO_SALARY"} <= codes
    assert status(client, admin, batch_id)["error_rows"] == 0
    assert client.post(f"/api/v1/imports/{batch_id}/confirm", headers=admin).status_code == 200
    run_worker()
    assert status(client, admin, batch_id)["status"] == "COMPLETED"

    head = client.get(f"/api/v1/personnel/cost-centers/{ccs[CC1]['id']}", headers=admin).json()
    view_url = f"/api/v1/personnel/submissions/{head['submission_id']}/view"
    people = _by_name(client.get(view_url, headers=admin).json())
    gil = people["GIL"]
    assert gil["movement"]["type"] == "TERMINATION" and gil["movement"]["month"] is None
    assert gil["movement"]["pending"] == ["month"] and gil["headcount"] == [1] * 12  # sem mês: segue o ano todo
    helo = people["HELO"]["movement"]
    assert helo["type"] == "SALARY_ADJUSTMENT" and helo["month"] == 6 and helo["pending"] == ["new_salary"]

    found = [i for i in client.get("/api/v1/findings", headers=admin).json()["items"] if i["module"] == "PERSONNEL"]
    month = next(i for i in found if i["kind"] == "PERSONNEL_NO_MONTH")
    assert month["subject"] == "Desligamento de GIL" and month["severity"] == "CRITICAL"
    assert month["fix"] == {"type": "month", "label": "Mês da ação"}
    assert any(i["kind"] == "PERSONNEL_NO_SALARY" and i["subject"] == "Reajuste individual de HELO" for i in found)
    sent = client.post(f"/api/v1/personnel/submissions/{head['submission_id']}/actions/submit", headers=admin, json={})
    assert sent.status_code == 409 and "mês da ação pendente: desligamento de GIL" in sent.json()["detail"]

    bad = client.post("/api/v1/findings/fix", headers=admin, json={"key": month["key"], "month": 13})
    assert bad.status_code == 422
    fixed = client.post("/api/v1/findings/fix", headers=admin, json={"key": month["key"], "month": 4})
    assert fixed.status_code == 200 and fixed.json()["note"] == "Mês da ação: ABR"
    gil = _by_name(client.get(view_url, headers=admin).json())["GIL"]
    assert gil["movement"]["month"] == 4 and gil["movement"]["pending"] == []
    assert gil["headcount"] == [1, 1, 1] + [0] * 9
    left = {i["key"] for i in client.get("/api/v1/findings", headers=admin).json()["items"]}
    assert month["key"] not in left


def test_inactive_contract_type_still_computes(client, admin, run_worker):
    ccs, mgr, _ = _setup(client, admin, run_worker)
    r = client.patch("/api/v1/contract-types/PJ", headers=admin, json={"is_active": False})
    assert r.status_code == 200, r.text
    head = client.get(f"/api/v1/personnel/cost-centers/{ccs[CC1]['id']}", headers=admin).json()
    view = client.get(f"/api/v1/personnel/submissions/{head['submission_id']}/view", headers=admin)
    assert view.status_code == 200 and _by_name(view.json())["CARLA"]["annual"] == "15750.00"
    assert client.get("/api/v1/consolidation/overview", headers=admin).status_code == 200
    # "Orçamento em construção" do Painel soma todos os tipos: o pessoal proposto entra (antes só OPEX)
    progress = client.get("/api/v1/dashboard/overview", headers=admin).json()["budget_progress"]
    assert progress["all_modules"] and Decimal(progress["proposed_total"]) > 0, progress
    people_only = client.get("/api/v1/dashboard/overview?modules=PERSONNEL", headers=admin).json()["budget_progress"]
    assert people_only["proposed_total"] == progress["proposed_total"]  # neste cenário só há pessoal
    opts = client.get("/api/v1/personnel/options", headers=admin).json()
    assert "PJ" not in {c["code"] for c in opts["contract_types"]}  # inativo não entra em novas vagas
    # transferência para CC que o gestor não gerencia é recusada
    ana = _by_name(view.json())["ANA"]["employee_id"]
    sub = head["submission_id"]
    resp = client.put(
        f"/api/v1/personnel/submissions/{sub}/employees/{ana}/movement",
        headers=mgr,
        json={"type": "TRANSFER", "month": 7, "target_cost_center_id": ccs[CC2]["id"], "reason": "x"},
    )
    assert resp.status_code == 422 and "gerencia" in resp.json()["detail"]


def test_findings_bulk_fix_keep_and_sector(client, admin, run_worker):
    """Apontamentos em lote: o mesmo mês para vários desligamentos sem mês; crítico não pode ser mantido; CC sem
    área e setor aparece para a Controladoria e é corrigido escolhendo o setor."""
    ccs, mgr, _ = _setup(client, admin, run_worker)
    rows = [
        ("700", "GIL", "ANALISTA", "1001", "0001", CC1, 6000, "REMOVER", None, None, None, "CLT", None, None),
        ("710", "IVO", "ANALISTA", "1001", "0001", CC1, 6100, "REMOVER", None, None, None, "CLT", None, None),
    ]
    import_and_load(client, admin, run_worker, builders.quadro_funcionarios(rows), "q-bulk.xlsx")
    data = client.get("/api/v1/findings", headers=admin).json()
    months = [i for i in data["items"] if i["kind"] == "PERSONNEL_NO_MONTH"]
    assert len(months) == 2
    structure = [i for i in data["items"] if i["kind"] == "STRUCTURE_NO_SECTOR"]
    assert [i["cost_center_id"] for i in structure] == [ccs[CC1]["id"]]
    assert structure[0]["editable"] is True and structure[0]["can_keep"] is False and data["sectors"]
    # gestor não vê o apontamento de estrutura (é da Controladoria)
    mine = client.get("/api/v1/findings", headers=mgr).json()
    assert not any(i["kind"] == "STRUCTURE_NO_SECTOR" for i in mine["items"]) and mine["sectors"] == []

    keys = [i["key"] for i in months]
    kept = client.post("/api/v1/findings/keep-many", headers=admin, json={"keys": keys, "note": "ok"}).json()
    assert kept["done"] == [] and len(kept["failed"]) == 2  # crítico: só corrigindo
    fixed = client.post("/api/v1/findings/fix-many", headers=admin, json={"keys": keys + ["0:X:1:Y"], "month": 5})
    assert fixed.status_code == 200, fixed.text
    assert [d["note"] for d in fixed.json()["done"]] == ["Mês da ação: MAI", "Mês da ação: MAI"]
    assert [f["key"] for f in fixed.json()["failed"]] == ["0:X:1:Y"]

    sector = next(s for s in data["sectors"] if s["label"].endswith("› Contabilidade"))
    bad = client.post("/api/v1/findings/fix", headers=admin, json={"key": structure[0]["key"]})
    assert bad.status_code == 422
    ok = client.post("/api/v1/findings/fix", headers=admin, json={"key": structure[0]["key"], "area_id": sector["id"]})
    assert ok.status_code == 200 and ok.json()["note"] == f"Setor: {sector['label']}"
    cc = next(c for c in client.get("/api/v1/cost-centers", headers=admin).json() if c["code"] == CC1)
    assert cc["area_id"] == sector["id"] and cc["department_id"]
    left = {i["kind"] for i in client.get("/api/v1/findings", headers=admin).json()["items"]}
    assert "STRUCTURE_NO_SECTOR" not in left and "PERSONNEL_NO_MONTH" not in left


def test_quadro_company_by_name_and_vacancy_without_salary(client, admin, run_worker):
    """Empresa escrita por nome ("ATEM") vira 1001 com aviso; "(VAGA ABERTA)" no nome é vaga; vaga sem salário entra
    pendente (sem custo) e aparece em Apontamentos para informar o salário."""
    ccs, _, _ = _setup(client, admin, run_worker)
    rows = [
        ("900", "JOAO", "ANALISTA", "ATEM", None, CC1, 7000, "MANTER", None, None, None, "CLT", None, None),
        (
            None,
            "FULANO (VAGA ABERTA)",
            "ANALISTA",
            "ATEM",
            None,
            CC1,
            5000,
            "MANTER",
            None,
            None,
            None,
            "CLT",
            None,
            None,
        ),
        (None, "-", "ANALISTA PL", "ATEM", None, CC1, None, "INCLUIR", 3, None, None, "CLT", None, None),
    ]
    batch_id = upload(client, admin, builders.quadro_funcionarios(rows), "q-nome.xlsx")
    run_worker()
    errors = client.get(f"/api/v1/imports/{batch_id}/errors", headers=admin).json()
    codes = {e["code"] for e in (errors["items"] if isinstance(errors, dict) else errors)}
    assert {"COMPANY_BY_NAME", "VACANCY_NO_SALARY"} <= codes
    assert status(client, admin, batch_id)["error_rows"] == 0
    assert client.post(f"/api/v1/imports/{batch_id}/confirm", headers=admin).status_code == 200
    run_worker()
    assert status(client, admin, batch_id)["status"] == "COMPLETED"
    found = [
        i for i in client.get("/api/v1/findings", headers=admin).json()["items"] if i["kind"] == "PERSONNEL_NO_SALARY"
    ]
    vaga = next(i for i in found if i["subject"] == "Vaga de ANALISTA PL")
    fixed = client.post("/api/v1/findings/fix", headers=admin, json={"key": vaga["key"], "amount": 6500})
    assert fixed.status_code == 200 and fixed.json()["note"] == "Novo salário: R$ 6.500,00"


def test_employee_without_cost_center_finding(client, admin, run_worker):
    """Colaborador ativo sem CC (planilha sem CC e cargo sem setor) aparece para a Controladoria em Apontamentos e é
    corrigido escolhendo o CC; o gestor não vê."""
    ccs, mgr, _ = _setup(client, admin, run_worker)
    rows = [("990", "ZECA", "CARGO SEM SETOR", "1001", None, None, 5000, "MANTER", None, None, None, "CLT", None, None)]
    import_and_load(client, admin, run_worker, builders.quadro_funcionarios(rows), "q-semcc.xlsx")
    data = client.get("/api/v1/findings", headers=admin).json()
    item = next(i for i in data["items"] if i["kind"] == "PERSONNEL_NO_CC")
    assert item["subject"] == "ZECA · CARGO SEM SETOR" and item["editable"] is True and item["can_keep"] is False
    assert any(o["label"].startswith(CC1) for o in item["fix"]["options"])
    assert not any(i["kind"] == "PERSONNEL_NO_CC" for i in client.get("/api/v1/findings", headers=mgr).json()["items"])
    assert (
        client.post("/api/v1/findings/fix", headers=mgr, json={"key": item["key"], "cost_center_id": 1}).status_code
        == 403
    )
    fixed = client.post(
        "/api/v1/findings/fix", headers=admin, json={"key": item["key"], "cost_center_id": ccs[CC1]["id"]}
    )
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["note"].startswith(f"Centro de custo: {CC1}")
    after = client.get("/api/v1/findings", headers=admin).json()
    assert not any(i["kind"] == "PERSONNEL_NO_CC" for i in after["items"])
    assert any(r["kind"] == "PERSONNEL_NO_CC" and r["action"] == "CORRECTED" for r in after["reviews"])
    head = client.get(f"/api/v1/personnel/cost-centers/{ccs[CC1]['id']}", headers=admin).json()
    view = client.get(f"/api/v1/personnel/submissions/{head['submission_id']}/view", headers=admin).json()
    assert "ZECA" in {p["name"] for p in view["positions"]}


def test_move_employee_to_other_cost_center(client, admin, run_worker):
    """Quadro importado sem CC e distribuído pelo cargo: a Controladoria corrige o CC do colaborador e as
    movimentações dele vão junto para o orçamento de pessoal do CC de destino (auditoria + Apontamentos)."""
    from tests import builders
    from tests.test_imports import import_and_load

    import_and_load(client, admin, run_worker, builders.opex_template_filled(), "t-move-opex.xlsx")
    ccs = client.get("/api/v1/cost-centers", headers=admin).json()
    src = ccs[0]
    other = client.post(
        "/api/v1/cost-centers",
        headers=admin,
        json={"company_id": src["company_id"], "code": "9990001", "name": "CC DESTINO"},
    ).json()
    head = client.get(f"/api/v1/personnel/cost-centers/{src['id']}", headers=admin).json()
    view = client.get(f"/api/v1/personnel/submissions/{head['submission_id']}/view", headers=admin).json()
    emp = next((p for p in view["positions"] if p["employee_id"]), None)
    if emp is None:
        return  # base sem quadro importado neste cenário
    r = client.put(
        f"/api/v1/personnel/employees/{emp['employee_id']}/cost-center",
        headers=admin,
        json={"cost_center_id": other["id"], "reason": "quadro veio sem CC"},
    )
    assert r.status_code == 200, r.text
    dst = client.get(f"/api/v1/personnel/cost-centers/{other['id']}", headers=admin).json()
    v2 = client.get(f"/api/v1/personnel/submissions/{dst['submission_id']}/view", headers=admin).json()
    assert [p["employee_id"] for p in v2["positions"]] == [emp["employee_id"]]
    v1 = client.get(f"/api/v1/personnel/submissions/{head['submission_id']}/view", headers=admin).json()
    assert emp["employee_id"] not in [p["employee_id"] for p in v1["positions"]]
    assert (
        client.put(
            f"/api/v1/personnel/employees/{emp['employee_id']}/cost-center",
            headers=admin,
            json={"cost_center_id": other["id"]},
        ).status_code
        == 409
    )


def test_exclude_employee_from_budget(client, admin, run_worker):
    """Controladoria retira o colaborador do orçamento: fica inativo, sai do quadro e as movimentações saem."""
    from tests import builders
    from tests.test_imports import import_and_load

    import_and_load(client, admin, run_worker, builders.opex_template_filled(), "t-excl.xlsx")
    src = client.get("/api/v1/cost-centers", headers=admin).json()[0]
    head = client.get(f"/api/v1/personnel/cost-centers/{src['id']}", headers=admin).json()
    view = client.get(f"/api/v1/personnel/submissions/{head['submission_id']}/view", headers=admin).json()
    emp = next((p for p in view["positions"] if p["employee_id"]), None)
    if emp is None:
        return
    assert (
        client.put(
            f"/api/v1/personnel/employees/{emp['employee_id']}/exclude", headers=admin, json={"reason": "x"}
        ).status_code
        == 422
    )
    r = client.put(
        f"/api/v1/personnel/employees/{emp['employee_id']}/exclude",
        headers=admin,
        json={"reason": "já está em outro orçamento"},
    )
    assert r.status_code == 200, r.text
    after = client.get(f"/api/v1/personnel/submissions/{head['submission_id']}/view", headers=admin).json()
    assert emp["employee_id"] not in [p["employee_id"] for p in after["positions"]]
    assert (
        client.put(
            f"/api/v1/personnel/employees/{emp['employee_id']}/exclude", headers=admin, json={"reason": "repetido"}
        ).status_code
        == 409
    )


def test_bonus_by_cc_spread_over_the_year(client, admin, run_worker):
    """Bônus CLT por CC (planilha da Controladoria, valor já com dissídio): total do ano diluído de jan a dez na conta
    6010101016, no quadro do CC e na consolidação."""
    ccs, _mgr, _final = _setup(client, admin, run_worker)
    head = client.get(f"/api/v1/personnel/cost-centers/{ccs[CC1]['id']}", headers=admin).json()
    url = f"/api/v1/personnel/submissions/{head['submission_id']}/view"
    before = client.get(url, headers=admin).json()["totals"]
    total_before = client.get("/api/v1/consolidation/overview", headers=admin).json()["modules"]["PERSONNEL"][
        "proposed"
    ]
    cycle_id = client.get("/api/v1/cycles", headers=admin).json()[0]["id"]
    r = client.put(
        f"/api/v1/cycles/{cycle_id}/parameters/personnel.bonus_by_cc",
        headers=admin,
        json={"value": {CC1: 42000, CC2: 12600.01}},
    )
    assert r.status_code == 200, r.text
    after = client.get(url, headers=admin).json()["totals"]
    assert after["cc_bonus_total"] == "42000.00" and after["cc_bonus_monthly"] == ["3500.00"] * 12
    assert Decimal(after["annual"]) - Decimal(before["annual"]) == Decimal("42000.00")
    assert after["charges_total"] == before["charges_total"]  # não passa pelo multiplicador
    total_after = client.get("/api/v1/consolidation/overview", headers=admin).json()["modules"]["PERSONNEL"]["proposed"]
    assert Decimal(total_after) - Decimal(total_before) == Decimal("54600.01")  # o CC2 entra mesmo sem quadro


def test_charges_split_overlap_finding_and_premises(client, admin, run_worker):
    """Rateio de encargos com as contas do abono (6010101009) e do bônus por CC (6010101016): apontamento do ciclo
    só para a Controladoria, página Premissas de pessoal e "Retirar do rateio" (total de pessoal não muda)."""
    ccs, mgr, _final = _setup(client, admin, run_worker)
    cycle_id = client.get("/api/v1/cycles", headers=admin).json()[0]["id"]

    def overlap_items(headers):
        items = client.get("/api/v1/findings", headers=headers).json()["items"]
        return [i for i in items if i["kind"] == "PERSONNEL_SPLIT_OVERLAP"]

    # sem abono e sem bônus por CC (o _setup zera o abono): nada sobreposto
    assert overlap_items(admin) == []
    assert client.get("/api/v1/personnel/premises", headers=admin).json()["overlap"] == []

    put = f"/api/v1/cycles/{cycle_id}/parameters"
    client.put(f"{put}/personnel.annual_bonus_clt", headers=admin, json={"value": 2500})
    client.put(f"{put}/personnel.bonus_by_cc", headers=admin, json={"value": {CC1: 12000}})
    [item] = overlap_items(admin)
    assert item["severity"] == "WARNING" and item["module"] == "PERSONNEL" and item["can_keep"] is False
    assert item["key"] == f"0:CYCLE:{cycle_id}:PERSONNEL_SPLIT_OVERLAP" and item["link"] == "/premissas-pessoal"
    assert "6010101009" in item["subject"] and "6010101016" in item["subject"] and Decimal(item["amount"]) > 0
    assert overlap_items(mgr) == []  # só a Controladoria
    keep = client.post("/api/v1/findings/keep", headers=admin, json={"key": item["key"], "note": "x"})
    assert keep.status_code == 409
    assert client.post("/api/v1/findings/fix", headers=admin, json={"key": item["key"]}).status_code == 409

    # página: formato e coerência com a consolidação
    assert client.get("/api/v1/personnel/premises", headers=mgr).status_code == 403
    p = client.get("/api/v1/personnel/premises", headers=admin).json()
    assert p["scenario"]["salary_adjustment_pct"] and p["scenario"]["adjustment_month"] == 1
    assert {m["code"] for m in p["multipliers"]} >= {"CLT", "PJ"}
    assert p["abono"]["value"] == "2500.00" and p["abono"]["account"]["code"] == "6010101009"
    assert Decimal(p["abono"]["total"]) > 0
    assert p["bonus_by_cc"]["account"]["code"] == "6010101016" and p["bonus_by_cc"]["total"] == "12000.00"
    assert [(r["code"], r["annual"], r["booked"]) for r in p["bonus_by_cc"]["rows"]] == [(CC1, "12000.00", "12000.00")]
    assert {o["code"] for o in p["overlap"]} == {"6010101009", "6010101016"}
    assert Decimal(p["overlap_amount"]) == Decimal(item["amount"])
    split = {r["code"]: r for r in p["charges"]["rows"]}
    assert split["6010101009"]["overlap"] and not split["6010102001"]["overlap"]
    assert sum(Decimal(r["amount"]) for r in p["charges"]["rows"]) == Decimal(p["charges"]["total"])
    assert abs(sum(Decimal(r["share"]) for r in p["charges"]["rows"]) - 1) < Decimal("0.000001")
    accounts = {a["code"]: a for a in p["accounts"]}
    assert Decimal(accounts["6010101009"]["total"]) == Decimal(p["abono"]["total"]) + Decimal(
        split["6010101009"]["amount"]
    )
    overview = client.get("/api/v1/consolidation/overview", headers=admin).json()
    total_before = overview["modules"]["PERSONNEL"]["proposed"]
    assert Decimal(p["total"]) == Decimal(total_before)

    # retirar do rateio: só Controladoria; total de pessoal igual; contas ficam só com o valor explícito
    assert client.post("/api/v1/personnel/premises/remove-overlap", headers=mgr).status_code == 403
    r = client.post("/api/v1/personnel/premises/remove-overlap", headers=admin)
    assert r.status_code == 200, r.text
    assert set(r.json()["removed"]) == {"6010101009", "6010101016"}
    after = r.json()["premises"]
    assert after["overlap"] == [] and after["total"] == p["total"]
    assert after["charges"]["total"] == p["charges"]["total"]
    assert not {"6010101009", "6010101016"} & {r["code"] for r in after["charges"]["rows"]}
    accounts = {a["code"]: a for a in after["accounts"]}
    assert accounts["6010101009"]["total"] == after["abono"]["total"]
    assert accounts["6010101016"]["total"] == "12000.00"
    overview = client.get("/api/v1/consolidation/overview", headers=admin).json()
    assert overview["modules"]["PERSONNEL"]["proposed"] == total_before
    assert overlap_items(admin) == []
    params = {x["key"]: x["value"] for x in client.get(f"{put}", headers=admin).json()}
    assert "6010101009" not in params["personnel.charges_split"] and "6010102001" in params["personnel.charges_split"]
    logs = client.get(
        "/api/v1/audit-logs",
        headers=admin,
        params={"entity_id": f"{cycle_id}:personnel.charges_split", "action": "SET_PARAMETER"},
    )
    assert logs.status_code == 200 and "PERSONNEL_SPLIT_OVERLAP" in logs.text
    again = client.post("/api/v1/personnel/premises/remove-overlap", headers=admin)
    assert again.status_code == 409


def test_premises_frozen_version_is_read_only(client, admin, run_worker):
    """Versão congelada: Premissas de pessoal só leitura (total da fotografia) e sem apontamento do ciclo."""
    _setup(client, admin, run_worker)
    cycle_id = client.get("/api/v1/cycles", headers=admin).json()[0]["id"]
    client.put(f"/api/v1/cycles/{cycle_id}/parameters/personnel.annual_bonus_clt", headers=admin, json={"value": 2500})
    live = client.get("/api/v1/personnel/premises", headers=admin).json()
    assert live["overlap"] and not live["frozen"]
    assert client.post("/api/v1/consolidation/freeze", headers=admin, json={"reason": "Fechamento"}).status_code == 200
    frozen = client.get("/api/v1/personnel/premises", headers=admin).json()
    assert frozen["frozen"] and frozen["total"] == live["total"]
    assert client.post("/api/v1/personnel/premises/remove-overlap", headers=admin).status_code == 409
    items = client.get("/api/v1/findings", headers=admin).json()["items"]
    assert not any(i["kind"] == "PERSONNEL_SPLIT_OVERLAP" for i in items)


def test_bonus_by_cc_counts_once_when_code_exists_in_two_companies(client, admin, run_worker):
    """O mesmo código de CC em duas empresas (ex.: criado na REAM pela importação do realizado) não duplica o bônus:
    fica com o CC da empresa de menor código."""
    ccs, _mgr, _final = _setup(client, admin, run_worker)
    companies = client.get("/api/v1/companies", headers=admin).json()
    cc1_company = ccs[CC1]["company_id"]
    other = next(c for c in companies if c["id"] != cc1_company)
    dup = client.post(
        "/api/v1/cost-centers",
        headers=admin,
        json={"company_id": other["id"], "code": CC1, "name": "Cópia REAM"},
    )
    assert dup.status_code in (200, 201), dup.text
    before = client.get("/api/v1/consolidation/overview", headers=admin).json()["modules"]["PERSONNEL"]["proposed"]
    cycle_id = client.get("/api/v1/cycles", headers=admin).json()[0]["id"]
    client.put(
        f"/api/v1/cycles/{cycle_id}/parameters/personnel.bonus_by_cc", headers=admin, json={"value": {CC1: 1200}}
    )
    after = client.get("/api/v1/consolidation/overview", headers=admin).json()["modules"]["PERSONNEL"]["proposed"]
    assert Decimal(after) - Decimal(before) == Decimal("1200.00")


WHAT_IF = "/api/v1/personnel/premises/what-if"


def _what_if_setup(client, admin, run_worker):
    """Quadro + abono de R$ 2.500 + bônus de R$ 12.000 no CC1 (o rateio do seed sobrepõe as duas contas)."""
    ccs, mgr, _final = _setup(client, admin, run_worker)
    cycle_id = client.get("/api/v1/cycles", headers=admin).json()[0]["id"]
    put = f"/api/v1/cycles/{cycle_id}/parameters"
    client.put(f"{put}/personnel.annual_bonus_clt", headers=admin, json={"value": 2500})
    client.put(f"{put}/personnel.bonus_by_cc", headers=admin, json={"value": {CC1: 12000}})
    return ccs, mgr, put


def _summary(out):
    return {s["key"]: s for s in out["summary"]}


def test_premises_what_if_current_equals_consolidation(client, admin, run_worker):
    """What-if das premissas (Controladoria): o lado "atual" é exatamente o da consolidação e das Premissas; sem
    alteração, diferença zero em tudo; nada é gravado."""
    _ccs, mgr, put = _what_if_setup(client, admin, run_worker)
    assert client.post(WHAT_IF, headers=mgr, json={}).status_code == 403
    params_before = client.get(put, headers=admin).json()
    r = client.post(WHAT_IF, headers=admin, json={})
    assert r.status_code == 200, r.text
    out = r.json()
    s = _summary(out)
    overview = client.get("/api/v1/consolidation/overview", headers=admin).json()
    premises = client.get("/api/v1/personnel/premises", headers=admin).json()
    assert Decimal(s["total"]["current"]) == Decimal(overview["modules"]["PERSONNEL"]["proposed"])
    assert s["total"]["current"] == premises["total"]
    assert s["salary"]["current"] == premises["salary"]["total"]
    assert s["charges"]["current"] == premises["charges"]["total"]
    assert s["bonus"]["current"] == premises["abono"]["total"]
    assert s["cc_bonus"]["current"] == premises["bonus_by_cc"]["booked"] == "12000.00"
    assert {a["code"]: a["total"] for a in premises["accounts"]} == {a["code"]: a["current"] for a in out["accounts"]}
    # sem alteração: zero em todos os quadros
    assert all(x["difference"] == "0.00" for x in out["summary"] + out["accounts"] + out["monthly"])
    assert all(x["difference"] == "0.00" for x in out["cost_centers"]["rows"] + out["areas"]["rows"])
    assert out["cost_centers"]["total"]["current"] == s["total"]["current"]
    assert sum(Decimal(m["current"]) for m in out["monthly"]) == Decimal(s["total"]["current"])
    assert out["figure"]["data"] and out["removed_from_split"] == []
    # nada gravado, mesmo simulando tudo
    everything = {
        "salary_adjustment_pct": 0.1,
        "adjustment_month": 3,
        "multipliers": {"CLT": 2.2},
        "annual_bonus": 9000,
        "cc_bonus_pct": 0.5,
        "remove_overlap": True,
        "include_severance": False,
    }
    assert client.post(WHAT_IF, headers=admin, json=everything).status_code == 200
    assert client.get(put, headers=admin).json() == params_before
    assert client.get("/api/v1/personnel/premises", headers=admin).json()["total"] == premises["total"]
    assert client.get("/api/v1/personnel/options", headers=admin).json()["scenario"]["multipliers"]["CLT"] != "2.2"


def test_premises_what_if_dissidio_and_multiplier(client, admin, run_worker):
    _what_if_setup(client, admin, run_worker)
    current = _summary(client.post(WHAT_IF, headers=admin, json={}).json())
    base = client.get("/api/v1/personnel/premises", headers=admin).json()["scenario"]
    pct = Decimal(base["salary_adjustment_pct"])
    # mesmo dissídio e mês do cenário base: nada muda
    same = {"salary_adjustment_pct": str(pct), "adjustment_month": base["adjustment_month"]}
    assert _summary(client.post(WHAT_IF, headers=admin, json=same).json())["total"]["difference"] == "0.00"
    out = client.post(WHAT_IF, headers=admin, json={"salary_adjustment_pct": str(pct + Decimal("0.05"))}).json()
    s = _summary(out)
    assert Decimal(s["salary"]["difference"]) > 0 and Decimal(s["charges"]["difference"]) > 0
    assert s["bonus"]["difference"] == s["cc_bonus"]["difference"] == s["severance"]["difference"] == "0.00"
    assert Decimal(s["total"]["difference"]) == Decimal(s["salary"]["difference"]) + Decimal(s["charges"]["difference"])
    assert Decimal(out["simulated_premises"]["salary_adjustment_pct"]) == pct + Decimal("0.05")
    # dissídio só a partir de julho custa menos que a partir de janeiro
    later = _summary(
        client.post(
            WHAT_IF, headers=admin, json={"salary_adjustment_pct": str(pct + Decimal("0.05")), "adjustment_month": 7}
        ).json()
    )
    assert Decimal(later["total"]["simulated"]) < Decimal(s["total"]["simulated"])
    # multiplicador do CLT: só a parte do multiplicador muda
    mult = _summary(client.post(WHAT_IF, headers=admin, json={"multipliers": {"CLT": 2.0}}).json())
    assert mult["salary"]["difference"] == "0.00" and Decimal(mult["charges"]["difference"]) > 0
    assert mult["total"]["current"] == current["total"]["current"]


def test_premises_what_if_abono_bonus_split_and_severance(client, admin, run_worker):
    _ccs, _mgr, put = _what_if_setup(client, admin, run_worker)
    # abono: só a conta do abono muda
    out = client.post(WHAT_IF, headers=admin, json={"annual_bonus": 5000}).json()
    s = _summary(out)
    assert Decimal(s["bonus"]["difference"]) > 0 and s["charges"]["difference"] == "0.00"
    assert s["total"]["difference"] == s["bonus"]["difference"]

    # bônus por CC: +5% e excluído
    plus = _summary(client.post(WHAT_IF, headers=admin, json={"cc_bonus_pct": 0.05}).json())
    assert plus["cc_bonus"]["simulated"] == "12600.00" and plus["total"]["difference"] == "600.00"
    off = _summary(client.post(WHAT_IF, headers=admin, json={"include_cc_bonus": False}).json())
    assert off["cc_bonus"]["simulated"] == "0.00" and off["total"]["difference"] == "-12000.00"

    # retirar do rateio: só redistribui entre contas (total igual)
    split = client.post(WHAT_IF, headers=admin, json={"remove_overlap": True}).json()
    assert set(split["removed_from_split"]) == {"6010101009", "6010101016"}
    ss = _summary(split)
    assert ss["total"]["difference"] == "0.00" and ss["charges"]["difference"] == "0.00"
    accounts = {a["code"]: a for a in split["accounts"]}
    assert accounts["6010101016"]["simulated"] == "12000.00"
    assert accounts["6010101009"]["simulated"] == ss["bonus"]["simulated"]
    assert Decimal(accounts["6010101009"]["difference"]) < 0
    assert all(m["difference"] == "0.00" for m in split["monthly"])

    # rescisórias fora
    sev = _summary(client.post(WHAT_IF, headers=admin, json={"include_severance": False}).json())
    assert sev["severance"]["simulated"] == "0.00"
    assert Decimal(sev["total"]["difference"]) == -Decimal(sev["severance"]["current"])

    # gravando os mesmos valores, as Premissas batem com o simulado (mesmo caminho de cálculo)
    client.put(f"{put}/personnel.annual_bonus_clt", headers=admin, json={"value": 5000})
    assert client.get("/api/v1/personnel/premises", headers=admin).json()["total"] == s["total"]["simulated"]
    split5000 = client.post(WHAT_IF, headers=admin, json={"remove_overlap": True}).json()
    assert client.post("/api/v1/personnel/premises/remove-overlap", headers=admin).status_code == 200
    applied = client.post(WHAT_IF, headers=admin, json={}).json()
    assert {a["code"]: a["current"] for a in applied["accounts"]} == {
        a["code"]: a["simulated"] for a in split5000["accounts"]
    }


def test_premises_what_if_validation(client, admin, run_worker):
    _what_if_setup(client, admin, run_worker)
    for body in (
        {"salary_adjustment_pct": 0.6},
        {"salary_adjustment_pct": -0.01},
        {"adjustment_month": 13},
        {"adjustment_month": 0},
        {"multipliers": {"CLT": 0}},
        {"multipliers": {"CLT": 5.5}},
        {"multipliers": {"XYZ": 1.5}},
        {"annual_bonus": -1},
        {"cc_bonus_pct": -1.5},
        {"cc_bonus_pct": 2.5},
    ):
        assert client.post(WHAT_IF, headers=admin, json=body).status_code == 422, body


def test_employee_without_registration_enters_with_provisional_key(client, admin, run_worker):
    """Colaborador sem matrícula (não é vaga): entra com matrícula provisória estável e aviso — antes a linha era
    descartada e o custo sumia do CC (caso da Especialista do Planejamento Tributário, 09/10/2026)."""
    ccs, _mgr, _final = _setup(client, admin, run_worker)
    rows = [*QUADRO, (None, "DORA SEM MATRICULA", "ESPECIALISTA", "1001", "0001", CC1, 11400, "MANTER")]
    rows[-1] = rows[-1] + (None, None, None, "CLT", "NÃO", "NÃO")
    final = import_and_load(client, admin, run_worker, builders.quadro_funcionarios(rows), "quadro2.xlsx", force=True)
    assert final["status"] == "COMPLETED", final
    head = client.get(f"/api/v1/personnel/cost-centers/{ccs[CC1]['id']}", headers=admin).json()
    view = client.get(f"/api/v1/personnel/submissions/{head['submission_id']}/view", headers=admin).json()
    dora = next(p for p in view["positions"] if p["name"] == "DORA SEM MATRICULA")
    assert dora["registration"].startswith("PROV-") and Decimal(dora["annual"]) > 0
    # reimportar o mesmo arquivo não duplica (a matrícula provisória é estável)
    import_and_load(client, admin, run_worker, builders.quadro_funcionarios(rows), "quadro3.xlsx", force=True)
    view = client.get(f"/api/v1/personnel/submissions/{head['submission_id']}/view", headers=admin).json()
    assert sum(1 for p in view["positions"] if p["name"] == "DORA SEM MATRICULA") == 1
