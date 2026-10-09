"""Defesa do orçamento (09/10/2026): questionar o lançamento em si (linha do OPEX), não o pacote."""

from decimal import Decimal
from pathlib import Path

from sqlalchemy import inspect, text

from app.db import engine
from app.models import Base
from tests import builders
from tests.test_imports import import_and_load
from tests.test_opex import _user


def _setup(client, admin, run_worker):
    import_and_load(client, admin, run_worker, builders.opex_template_filled(), "t-q-linha.xlsx")
    ccs = {c["code"]: c for c in client.get("/api/v1/cost-centers", headers=admin).json()}
    cc_a, cc_b = ccs["1050101011"]["id"], ccs["1050101012"]["id"]
    mgr_a_id, mgr_a = _user(client, admin, "gestor.a.linha@t.com", ["MANAGER"])
    mgr_b_id, mgr_b = _user(client, admin, "gestor.b.linha@t.com", ["MANAGER"])
    for cc, mgr in ((cc_a, mgr_a_id), (cc_b, mgr_b_id)):
        r = client.patch(f"/api/v1/cost-centers/{cc}", headers=admin, json={"manager_user_id": mgr})
        assert r.status_code == 200, r.text
    return cc_a, cc_b, mgr_a, mgr_b


def _lines(client, headers, **params):
    r = client.get("/api/v1/dashboard/why/lines", headers=headers, params={"modules": "OPEX", **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_question_on_budget_line(client, admin, run_worker):
    """A pergunta vai para o gestor do CC da linha, guarda a linha como estava (valor na pergunta × valor atual) e
    continua de pé quando a linha é editada, movida de CC ou excluída."""
    cc_a, cc_b, mgr_a, mgr_b = _setup(client, admin, run_worker)

    # lançamentos do recorte no "por quê?": maiores primeiro, com busca e paginação
    page = _lines(client, admin, cost_center_id=cc_a, limit=2)
    assert page["count"] >= 2 and len(page["items"]) == 2
    totals = [Decimal(i["total"]) for i in page["items"]]
    assert totals == sorted(totals, reverse=True) and all(t != 0 for t in totals)
    line = page["items"][0]
    assert line["cost_center_id"] == cc_a and len(line["values"]) == 12 and line["account"]
    code = line["account"].split()[0]
    found = _lines(client, admin, q=code)
    assert found["items"] and all(i["account"].startswith(code) for i in found["items"])
    assert _lines(client, admin, modules="CAPEX")["count"] == 0
    assert _lines(client, admin, cost_center_id=cc_a, offset=1, limit=1)["items"][0]["id"] == page["items"][1]["id"]

    body = {"item_type": "OPEX_LINE", "item_id": line["id"], "question": "Que contrato é este?"}
    asked = client.post("/api/v1/questions", headers=admin, json=body)
    assert asked.status_code == 201, asked.text
    q = asked.json()
    snap = q["item"]["snapshot"]
    assert q["item_type"] == "OPEX_LINE" and q["cost_center_id"] == cc_a and q["subject"]
    assert snap["id"] == line["id"] and snap["total"] == line["total"] and snap["account"] == line["account"]
    assert q["item"]["link"].startswith(f"/orcamento/{cc_a}?pacote=")
    assert q["item"]["link"].endswith(f"&linha={line['id']}")
    assert not (q["item"]["changed"] or q["item"]["moved"] or q["item"]["deleted"])
    # "Questionar" no orçamento do CC: Controladoria sim; o próprio gestor do CC não
    assert client.get(f"/api/v1/opex/cost-centers/{cc_a}", headers=admin).json()["permissions"]["ask"] is True
    assert client.get(f"/api/v1/opex/cost-centers/{cc_a}", headers=mgr_a).json()["permissions"]["ask"] is False
    assert _lines(client, admin, cost_center_id=cc_a, limit=2)["items"][0]["open_questions"] == 1

    # escopo: o gestor do CC da linha vê e responde; o gestor de outro CC não
    inbox_a = client.get("/api/v1/questions?to_answer=true", headers=mgr_a).json()
    assert [x["id"] for x in inbox_a["items"]] == [q["id"]]
    assert q["id"] not in {x["id"] for x in client.get("/api/v1/questions", headers=mgr_b).json()["items"]}
    assert client.post(f"/api/v1/questions/{q['id']}/answer", headers=mgr_b, json={"answer": "x"}).status_code == 404
    # nem pergunta sobre a linha de um CC que não enxerga; lançamento inexistente é recusado
    assert client.post("/api/v1/questions", headers=mgr_b, json=body).status_code == 403
    assert client.post("/api/v1/questions", headers=admin, json=body | {"item_id": 0}).status_code == 422

    # a linha muda: a pergunta guarda o valor da hora da pergunta e mostra o atual
    patched = client.patch(f"/api/v1/opex/lines/{line['id']}", headers=admin, json={"values": {"1": "123456.78"}})
    assert patched.status_code == 200, patched.text
    item = client.get("/api/v1/questions", headers=mgr_a).json()["items"][0]["item"]
    assert item["snapshot"]["total"] == line["total"] and item["changed"]
    assert item["current"]["total"] != line["total"]

    # a linha vai para outro CC: o gestor do CC novo passa a ver e responder; o retrato continua no CC original
    moved = client.post(
        f"/api/v1/opex/lines/{line['id']}/move",
        headers=admin,
        json={"target_cost_center_id": cc_b, "reason": "CC errado na planilha"},
    )
    assert moved.status_code == 200, moved.text
    seen_b = client.get("/api/v1/questions?to_answer=true", headers=mgr_b).json()["items"]
    assert [x["id"] for x in seen_b] == [q["id"]]
    assert seen_b[0]["item"]["moved"] and seen_b[0]["item"]["snapshot"]["cost_center_id"] == cc_a
    assert seen_b[0]["item"]["current"]["cost_center_id"] == cc_b
    ans = client.post(f"/api/v1/questions/{q['id']}/answer", headers=mgr_b, json={"answer": "Contrato de limpeza"})
    assert ans.status_code == 200 and ans.json()["status"] == "ANSWERED"

    # excluída: a pergunta fica (FK nula) com o retrato
    assert client.delete(f"/api/v1/opex/lines/{line['id']}", headers=admin).status_code in (200, 204)
    gone = next(x for x in client.get("/api/v1/questions", headers=mgr_a).json()["items"] if x["id"] == q["id"])
    assert gone["item"]["deleted"] and gone["item"]["link"] is None
    assert gone["item"]["snapshot"]["total"] == line["total"] and gone["answer"] == "Contrato de limpeza"

    logs = client.get("/api/v1/audit-logs?entity_type=budget_question", headers=admin).json()
    rows = logs["items"] if isinstance(logs, dict) else logs
    assert {"QUESTION", "ANSWER"} <= {x["action"] for x in rows}


def test_package_level_questions_still_listed(client, admin, run_worker):
    """Perguntas antigas (sobre um recorte/pacote, sem lançamento) continuam listadas, no "por quê?" e respondíveis."""
    cc_a, _, mgr_a, mgr_b = _setup(client, admin, run_worker)
    body = {"subject": "Pacote DTI", "question": "Por que o pacote cresceu?", "scope": {"cost_center_ids": [cc_a]}}
    old = client.post("/api/v1/questions", headers=admin, json=body)
    assert old.status_code == 201, old.text
    qid = old.json()["id"]
    assert old.json()["item"] is None and old.json()["item_type"] is None
    assert [x["id"] for x in client.get("/api/v1/questions?to_answer=true", headers=mgr_a).json()["items"]] == [qid]
    assert qid not in {x["id"] for x in client.get("/api/v1/questions", headers=mgr_b).json()["items"]}
    why = client.get(f"/api/v1/dashboard/why?modules=OPEX&cost_center_id={cc_a}", headers=admin).json()
    assert qid in {x["id"] for x in why["questions"]}
    r = client.post(f"/api/v1/questions/{qid}/answer", headers=mgr_a, json={"answer": "Novo sistema"})
    assert r.status_code == 200 and r.json()["status"] == "ANSWERED"


def test_migration_0012_up_down_without_drift():
    from alembic.autogenerate import compare_metadata
    from alembic.config import Config
    from alembic.migration import MigrationContext

    from alembic import command

    backend = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.execute(text("drop table if exists alembic_version"))
    new = {"item_type", "budget_line_id", "personnel_movement_id", "capex_project_id", "item_snapshot"}
    try:
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
        assert diff == [], diff
        command.downgrade(cfg, "0011")
        assert not {c["name"] for c in inspect(engine).get_columns("budget_questions")} & new
        command.upgrade(cfg, "head")
        assert new <= {c["name"] for c in inspect(engine).get_columns("budget_questions")}
    finally:
        with engine.begin() as conn:
            conn.execute(text("drop table if exists alembic_version"))
