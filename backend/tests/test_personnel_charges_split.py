"""Rateio da parte do multiplicador (encargos e benefícios) entre contas: `personnel.charges_split`."""

from decimal import Decimal

from app.domain.rules.personnel import normalize_split, split_amount
from app.models import CycleParameter
from tests.test_consolidation import _setup

KEY = "personnel.charges_split"
SALARY = "267750.00"  # salário com reajuste do QUADRO (6010101001)
CHARGES = Decimal("201600.00")  # parte do multiplicador do QUADRO
PERSONNEL_TOTAL = "469350.00"


# ------------------------------------------------------------------ regra pura


def test_normalize_split_weights():
    assert normalize_split(None) == [] and normalize_split({}) == [] and normalize_split([1, 2]) == []
    # inválidos, zerados e negativos ficam de fora; sobrando nada, vale a conta única
    assert normalize_split({"a": 0, "b": -1, "c": "x", "d": True}) == []
    # soma ≠ 1 é normalizada; maior peso primeiro
    assert normalize_split({"2": 1, "1": 3}) == [("1", Decimal("0.75")), ("2", Decimal("0.25"))]
    assert normalize_split({"1": 75, "2": 25}) == normalize_split({"1": 0.75, "2": 0.25})
    assert normalize_split({" 1 ": "2", "2": 2.0}) == [("1", Decimal("0.5")), ("2", Decimal("0.5"))]


def test_split_amount_sums_exactly():
    split = normalize_split({"a": 1, "b": 1, "c": 1})
    for amount in (Decimal("100"), Decimal("100.01"), Decimal("0.02"), Decimal("-100.01"), Decimal("16800.004")):
        shares = split_amount(amount, split)
        assert sum(shares.values()) == amount.quantize(Decimal("0.01"))
    shares = split_amount(Decimal("100.00"), split)
    assert shares == {"a": Decimal("33.34"), "b": Decimal("33.33"), "c": Decimal("33.33")}  # centavo na maior (1ª)
    # pesos irregulares (mix padrão): soma exata em vários valores
    from app.seed_data import CYCLE_PARAMETERS

    mix = normalize_split(CYCLE_PARAMETERS[KEY][0])
    assert mix[0][0] == "6010102001" and abs(sum(w for _c, w in mix) - 1) < Decimal("1e-20")
    for cents in range(1, 5000, 37):
        amount = Decimal(cents) / 100 + Decimal("12345")
        assert sum(split_amount(amount, mix).values()) == amount
    assert split_amount(Decimal("10"), []) == {}


# ------------------------------------------------------------------ consolidação


def _personnel(client, admin) -> dict[str, Decimal]:
    ov = client.get("/api/v1/consolidation/overview", headers=admin).json()
    assert ov["modules"]["PERSONNEL"]["proposed"] == PERSONNEL_TOTAL  # total nunca muda com o rateio
    return {
        v["account"]: Decimal(v["proposed"])
        for v in ov["variations"]
        if v["module"] == "PERSONNEL" and Decimal(v["proposed"])
    }


def _set(client, admin, value):
    cycle_id = client.get("/api/v1/cycles", headers=admin).json()[0]["id"]
    return client.put(f"/api/v1/cycles/{cycle_id}/parameters/{KEY}", headers=admin, json={"value": value})


def test_default_split_keeps_total_and_spreads_charges(client, admin, run_worker):
    _setup(client, admin, run_worker)
    acc = _personnel(client, admin)
    assert acc.pop("6010101001") == Decimal(SALARY)
    assert sum(acc.values()) == CHARGES
    # INSS fica com ~25,4% (25,30 / 99,70) da parte do multiplicador, não com tudo
    assert abs(acc["6010102001"] - CHARGES * Decimal("25.30") / Decimal("99.70")) < Decimal("0.13")
    assert acc["6010103003"] > acc["6010103002"] > acc["6010101008"] > 0

    # mês a mês e por CC também fecha com a conta única (Carga SAP, Painel e "por quê?" leem as mesmas linhas)
    import dataclasses

    from app.db import SessionLocal
    from app.services import consolidation as cons
    from app.services import opex as opex_svc

    def by_cc_month(rows):
        out = {}
        for r in rows:
            if r.module == "PERSONNEL" and r.account_code != "6010101001":
                for m, v in enumerate(r.values):
                    out[(r.cost_center_id, m)] = out.get((r.cost_center_id, m), Decimal(0)) + v
        return out

    with SessionLocal() as db:
        ctx = opex_svc.context(db)
        split_rows = cons.rows_for(db, ctx, ctx.version)
        single = dataclasses.replace(ctx, params={**ctx.params, KEY: {}})
        single_rows = cons.rows_for(db, single, ctx.version)
    assert by_cc_month(split_rows) == by_cc_month(single_rows)
    assert {r.account_code for r in single_rows if r.module == "PERSONNEL"} == {"6010101001", "6010102001"}

    # ponto de atenção para as contas do rateio fora do cadastro
    points = client.get("/api/v1/consolidation/attention-points", headers=admin).json()["points"]
    assert any("rateio de encargos" in p["message"] for p in points if p["kind"] == "ACCOUNT_CONFIG")


def test_without_split_keeps_single_charges_account(client, admin, run_worker, db):
    _setup(client, admin, run_worker)
    # parâmetro ausente (ciclo anterior ao rateio): comportamento antigo, tudo em personnel.charges_account
    cycle_id = client.get("/api/v1/cycles", headers=admin).json()[0]["id"]
    db.delete(db.get(CycleParameter, (cycle_id, KEY)))
    db.commit()
    assert _personnel(client, admin) == {"6010101001": Decimal(SALARY), "6010102001": CHARGES}
    # vazio: idem
    assert _set(client, admin, {}).status_code == 200
    assert _personnel(client, admin) == {"6010101001": Decimal(SALARY), "6010102001": CHARGES}


def test_split_weights_are_normalized(client, admin, run_worker):
    _setup(client, admin, run_worker)
    assert _set(client, admin, {"6010102001": 3, "6010102002": 1}).status_code == 200
    a = _personnel(client, admin)
    assert _set(client, admin, {"6010102001": 0.75, "6010102002": 0.25}).status_code == 200
    b = _personnel(client, admin)
    assert (
        a == b == {"6010101001": Decimal(SALARY), "6010102001": Decimal("151200.00"), "6010102002": Decimal("50400.00")}
    )

    # validação do parâmetro
    for bad in ([1], {"6010102001": -1}, {"6010102001": "x"}, {"abc": 1}, {"6010102001": 0}):
        resp = _set(client, admin, bad)
        assert resp.status_code == 422, bad
    assert _personnel(client, admin)["6010102002"] == Decimal("50400.00")  # nada mudou
