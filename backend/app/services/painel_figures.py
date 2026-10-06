"""Figuras Plotly do Painel 2 (o Painel desenhado em Plotly, para comparação com a versão em SVG).

A entrada é o próprio dicionário de `GET /dashboard/overview`: os números são exatamente os do Painel, só o
desenho muda. Paleta do Painel: azul = realizado, verde-água = orçado/orçamento, cinza = ano anterior; barras
com degradê do maior para o menor e rótulos pt-BR. As figuras vão em JSON para `components/PlotlyChart.tsx`,
que usa o bundle básico do Plotly (barras e linhas) — por isso o mapa de calor continua em HTML no frontend.
"""

from decimal import Decimal

import plotly.graph_objects as go

from app.services.analytics import _fig, _layout, _money_axis, fmt_compact, fmt_money, fmt_pct, short

MONTHS = ("JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ")
REALIZADO, ORCADO, ANTERIOR = "#4472c4", "#4fa894", "#8b95a7"
TXT_UP, TXT_DOWN, TXT_FLAT = "#c13515", "#008a05", "#767676"  # percentual no texto, como no Painel


def _num(value) -> float:
    return float(Decimal(str(value or 0)))


def _rgba(color: str, alpha: float) -> str:
    h = color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha:.2f})"


def _shades(color: str, values: list[float], floor: float = 0.45) -> list[str]:
    """Degradê do maior para o menor: a maior barra em cor cheia, as menores mais claras (até `floor`)."""
    ranked = sorted({v for v in values if v > 0}, reverse=True)
    if len(ranked) <= 1:
        return [_rgba(color, 1.0)] * len(values)
    step = (1 - floor) / (len(ranked) - 1)
    return [_rgba(color, 1.0 - ranked.index(v) * step) if v > 0 else _rgba(color, 1.0) for v in values]


def _short(value: float) -> str:
    """Rótulo dentro do gráfico: "1,5 mi" em vez de "R$ 1,5 mi"."""
    return fmt_compact(value).replace("R$ ", "")


def _category(name: str | None, code: str | None) -> str:
    """Nome da categoria no eixo: único (o código diferencia nomes iguais) e curto (o nome inteiro vai no hover)."""
    name = name or code or "—"
    return f"{short(name, 26)} · {code}" if code else short(name, 34)


def _labels(o: dict) -> dict:
    p = o["period"]
    prev_series = f"Realizado {o['previous_year']}" if p["annualized_base"] and o["previous_year"] else p["base_label"]
    return {
        "actual": p["actual_label"] or "Realizado",
        "budget": p["budget_label"] or "Orçado",
        "base": p["base_label"] or "",
        "prev_series": prev_series or "",
        "main": p["main_label"],
        "main_color": REALIZADO if p["main"] == "actual" else ORCADO,
        "base_color": ORCADO if p["base_kind"] == "budget" else ANTERIOR,
    }


def fig_monthly(o: dict) -> dict:
    """Comparativo mensal: colunas lado a lado (ano anterior, realizado, orçado), degradê em cada série e o valor
    em cada coluna quando cabe. O clique devolve o mês (customdata[1]) para filtrar o painel."""
    lb = _labels(o)
    series = []
    if o["has_prev"]:
        series.append(("prev", lb["prev_series"], ANTERIOR))
    if o["has_actual"]:
        series.append(("ref", lb["actual"], REALIZADO))
    if o["has_budget"]:
        series.append(("budget", lb["budget"], ORCADO))
    data, everything = [], []
    for key, name, color in series:
        values = [_num(r[key]) for r in o["monthly"]]
        everything += values
        data.append(
            go.Bar(
                x=list(MONTHS),
                y=values,
                name=name,
                marker={"color": _shades(color, values), "cornerradius": 4},
                text=[_short(v) if v > 0 else "" for v in values],
                textposition="outside",
                cliponaxis=False,
                textfont={"size": 11, "color": color},
                customdata=[[fmt_money(v), m] for m, v in enumerate(values, start=1)],
                hovertemplate=name + ": <b>%{customdata[0]}</b><extra></extra>",
            )
        )
    layout = _layout(
        barmode="group",
        bargap=0.22,
        bargroupgap=0.06,
        hovermode="x unified",
        uniformtext={"minsize": 9, "mode": "hide"},  # rótulo só quando cabe na coluna
        yaxis=_money_axis(everything),
        xaxis={"showgrid": False, "automargin": True, "fixedrange": True},
        margin={"l": 8, "r": 8, "t": 36, "b": 8},
    )
    return _fig(data, layout)


def fig_cumulative(o: dict) -> dict:
    """Total acumulado: linha pontilhada do realizado (até o último mês com valor) sobre a área da base (orçado ou
    ano anterior). Em cada mês, o rótulo da série mais alta fica acima do ponto e o da mais baixa, abaixo."""
    lb = _labels(o)
    rows = o["monthly"]

    def cum(key: str) -> list[float]:
        out, total = [], 0.0
        for r in rows:
            total += _num(r[key])
            out.append(total)
        return out

    last_ref = max((i for i, r in enumerate(rows) if _num(r["ref"]) > 0), default=-1)
    ref = cum("ref")[: last_ref + 1] if o["has_actual"] else []
    base_key = "budget" if o["has_budget"] else ("prev" if o["has_prev"] else None)
    last_base = max((i for i, r in enumerate(rows) if base_key and _num(r[base_key]) > 0), default=-1)
    base = cum(base_key)[: last_base + 1] if base_key else []
    base_name = lb["budget"] if base_key == "budget" else lb["prev_series"]
    base_color = ORCADO if base_key == "budget" else ANTERIOR
    data = []
    if base:
        pos = ["bottom center" if (i < len(ref) and ref[i] > v) else "top center" for i, v in enumerate(base)]
        data.append(
            go.Scatter(
                x=list(MONTHS[: len(base)]),
                y=base,
                name=base_name,
                mode="lines+markers+text",
                line={"color": base_color, "width": 2},
                marker={"size": 6},
                fill="tozeroy",
                fillcolor=_rgba(base_color, 0.14),
                text=[_short(v) for v in base],
                textposition=pos,
                textfont={"size": 10, "color": base_color},
                cliponaxis=False,
                customdata=[fmt_money(v) for v in base],
                hovertemplate=base_name + ": <b>%{customdata}</b><extra></extra>",
            )
        )
    if ref:
        pos = ["top center" if (not base or v >= base[i]) else "bottom center" for i, v in enumerate(ref)]
        data.append(
            go.Scatter(
                x=list(MONTHS[: len(ref)]),
                y=ref,
                name=lb["actual"],
                mode="lines+markers+text",
                line={"color": REALIZADO, "width": 2.5, "dash": "dot"},
                marker={"size": 7},
                fill="tozeroy",
                fillcolor=_rgba(REALIZADO, 0.12),
                text=[_short(v) for v in ref],
                textposition=pos,
                textfont={"size": 10, "color": REALIZADO},
                cliponaxis=False,
                customdata=[fmt_money(v) for v in ref],
                hovertemplate=lb["actual"] + ": <b>%{customdata}</b><extra></extra>",
            )
        )
    layout = _layout(
        hovermode="x unified",
        yaxis=_money_axis(base + ref),
        xaxis={
            "showgrid": False,
            "automargin": True,
            "fixedrange": True,
            "categoryorder": "array",
            "categoryarray": list(MONTHS),
        },
        margin={"l": 8, "r": 24, "t": 36, "b": 8},
    )
    return _fig(data, layout)


def fig_rank(rows: list[dict], lb: dict, show_base: bool) -> dict:
    """Barras horizontais do maior para o menor, com degradê; com base, barra fina abaixo de cada barra e a
    variação % no texto. O clique devolve o id da linha (customdata[3]) para filtrar o painel."""
    rows = [r for r in rows if _num(r["value"]) or _num(r.get("base"))]
    cats = [_category(r["name"], r.get("code")) for r in rows]
    values = [_num(r["value"]) for r in rows]
    bases = [_num(r.get("base")) for r in rows]

    def text(value: float, var) -> str:
        out = f"<b>{fmt_compact(value)}</b>"
        if show_base and var is not None:
            n = float(var)
            color = TXT_UP if n > 0 else TXT_DOWN if n < 0 else TXT_FLAT
            out += f"  <span style='color:{color};font-size:11px'>{fmt_pct(n)}</span>"
        return out

    hover = "<b>%{y}</b><br>" + lb["main"] + ": %{customdata[0]}"
    if show_base:
        hover += "<br>" + lb["base"] + ": %{customdata[1]}<br>Variação: %{customdata[2]}"
    data = [
        go.Bar(
            orientation="h",
            x=values,
            y=cats,
            name=lb["main"],
            width=0.6,
            offset=-0.42 if show_base else -0.3,
            marker={"color": _shades(lb["main_color"], values), "cornerradius": 4},
            text=[text(v, r.get("var_pct")) for v, r in zip(values, rows, strict=True)],
            textposition="outside",
            cliponaxis=False,
            textfont={"size": 12},
            customdata=[
                [
                    fmt_money(v),
                    fmt_money(b),
                    fmt_pct(r["var_pct"]) if r.get("var_pct") is not None else "—",
                    r.get("id"),
                ]
                for v, b, r in zip(values, bases, rows, strict=True)
            ],
            hovertemplate=hover + "<extra></extra>",
        )
    ]
    if show_base:
        data.append(
            go.Bar(
                orientation="h",
                x=bases,
                y=cats,
                name=lb["base"],
                width=0.16,
                offset=0.24,
                marker={"color": _rgba(lb["base_color"], 0.85), "cornerradius": 3},
                hoverinfo="skip",
            )
        )
    top = max([*values, *bases, 0.0])
    layout = _layout(
        barmode="overlay",
        showlegend=False,
        height=max(220, 44 * len(rows) + 30),
        xaxis={
            "showticklabels": False,
            "showgrid": False,
            "zeroline": False,
            "range": [0, top * (1.75 if show_base else 1.4) or 1],
            "fixedrange": True,
        },
        yaxis={"autorange": "reversed", "automargin": True, "tickfont": {"size": 12}, "fixedrange": True},
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
    )
    return _fig(data, layout)


def build(o: dict) -> dict:
    """Todas as figuras do Painel 2 a partir da resposta de `/dashboard/overview`."""
    lb = _labels(o)
    show_base = bool(o["period"]["base_kind"])

    def ranked(rows: list[dict]) -> list[dict]:
        return [
            {
                "id": r["id"],
                "code": r["code"],
                "name": r["name"],
                "value": r["ref_ytd"],
                "base": r["prev_ytd"],
                "var_pct": r["ytd_var_pct"],
            }
            for r in rows
        ]

    figures = {
        "monthly": fig_monthly(o),
        "cumulative": fig_cumulative(o),
        "top_cost_centers": fig_rank(ranked(o["top_cost_centers"]), lb, show_base),
        "top_accounts": fig_rank(ranked(o["top_accounts"]), lb, show_base),
    }
    return figures
