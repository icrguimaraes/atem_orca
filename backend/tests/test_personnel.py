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
    return ccs, mgr, final


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

    # desligamento e contratação exigem justificativa para enviar
    resp = client.post(f"/api/v1/personnel/submissions/{sub}/actions/submit", headers=mgr, json={})
    assert resp.status_code == 409 and "justificativa" in resp.json()["detail"]
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
