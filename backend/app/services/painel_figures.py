"""Figuras Plotly do Painel 2 (o Painel desenhado em Plotly, para comparação com a versão em SVG).

A entrada é o próprio dicionário de `GET /dashboard/overview`: os números são exatamente os do Painel, só o
desenho muda. Paleta do Painel: azul = realizado, verde-água = orçado/orçamento, cinza = ano anterior; barras
com degradê do maior para o menor e rótulos pt-BR. As figuras vão em JSON para `components/PlotlyChart.tsx`,
que usa o bundle básico do Plotly (barras e linhas) — por isso o mapa de calor continua em HTML no frontend.
"""

import re
from decimal import Decimal

import plotly.graph_objects as go

from app.services.analytics import _br, _fig, _layout, fmt_compact, fmt_money, fmt_pct, short

MONTHS = ("JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ")
REALIZADO, ORCADO, ANTERIOR = "#4472c4", "#4fa894", "#8a6bbf"  # azul, verde-água (orçamento), roxo
# rótulos no tom escuro de cada série (legíveis em 10–11 px); o frontend troca pelo token do tema escuro
INK = {REALIZADO: "#3a64b4", ORCADO: "#2b7a68", ANTERIOR: "#6a4c9c"}
TXT_UP, TXT_DOWN, TXT_FLAT = "#c13515", "#008a05", "#767676"  # percentual no texto, como no Painel
# variação na cascata da "Evolução do orçamento": areia (âmbar dessaturado), fora das cores de série e sem laranja
VARIACAO = "#c8ad5e"
INK[VARIACAO] = "#7a6524"
GUIDE = "rgba(128,128,128,0.6)"
# legenda sempre visível (o Plotly a esconde com uma série só) e sem clique: a cor diz o que é realizado/orçado
LEGEND = {
    "orientation": "h",
    "yanchor": "bottom",
    "y": 1.02,
    "x": 0,
    "font": {"size": 12},
    "itemclick": False,
    "itemdoubleclick": False,
}  # linhas tracejadas e seta do total (neutras nos dois temas)


def _num(value) -> float:
    return float(Decimal(str(value or 0)))


def _rgba(color: str, alpha: float) -> str:
    h = color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha:.2f})"


def _signed_money(value: float) -> str:
    """Diferença em reais com sinal, por extenso: +R$ 1.234,56 · -R$ 1.234,56."""
    return f"{'+' if value > 0 else '-' if value < 0 else ''}{fmt_money(abs(value))}"


def _tip_label(label: str | None) -> str:
    """Rótulo curto para o tooltip mensal: sem "até SET" e sem o parêntese (o mês já está no título)."""
    return re.sub(r" \(.*\)$| até [A-Z]{3}$", "", label or "").strip()


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
        # tooltip: no mês basta o ano ("Realizado 2026"); nos rankings vai o período inteiro ("… até SET")
        "tip": {
            "prev": f"Realizado {o['previous_year']}" if o.get("previous_year") else _tip_label(p["base_label"]),
            "ref": _tip_label(p["actual_label"]) or "Realizado",
            "budget": p["budget_label"] or "Orçado",
        },
    }


def fig_monthly(o: dict, monthly: list[dict] | None = None, months: list[int] | None = None) -> dict:
    """Comparativo mensal: colunas lado a lado (ano anterior, realizado, orçado) em cor sólida, com o valor em cada
    coluna quando cabe. customdata = [valor, mês (o clique filtra), linhas do tooltip]."""
    lb = _labels(o)
    series = []
    if o["has_prev"]:
        series.append(("prev", lb["prev_series"], ANTERIOR))
    if o["has_actual"]:
        series.append(("ref", lb["actual"], REALIZADO))
    if o["has_budget"]:
        series.append(("budget", lb["budget"], ORCADO))
    rows = monthly or o["monthly"]
    picked = set(months or [])
    opacity = [1.0 if (not picked or m in picked) else 0.3 for m in range(1, 13)]
    values_by = {key: [_num(r[key]) for r in rows] for key, _, _ in series}
    zeros = [0.0] * 12
    data, everything = [], []
    for key, name, color in series:
        values = values_by[key]
        everything += values
        tips = []
        for i, v in enumerate(values):
            lines = [[f"{lb['tip'][key]}: {fmt_money(v)}", color]] if v else []
            if key == "ref" and v:
                prev, budget = values_by.get("prev", zeros)[i], values_by.get("budget", zeros)[i]
                if prev:
                    lines.append([f"Variação: {fmt_pct((v - prev) / prev)} ({_signed_money(v - prev)})", ""])
                if budget:
                    lines.append(
                        [f"Realizado vs orçado: {fmt_pct((v - budget) / budget)} ({_signed_money(v - budget)})", ""]
                    )
            tips.append(lines)
        data.append(
            go.Bar(
                x=list(MONTHS),
                y=values,
                name=name,
                marker={"color": color, "cornerradius": 4, "opacity": opacity},
                text=[_short(v) if v > 0 else "" for v in values],
                textposition="outside",
                cliponaxis=False,
                textfont={
                    "size": 13,
                    # sem seleção, uma cor só; com meses escolhidos, os rótulos dos outros meses esmaecem
                    "color": [INK[color] if a == 1.0 else _rgba(INK[color], 0.4) for a in opacity]
                    if picked
                    else INK[color],
                },
                customdata=[[fmt_money(v), m, t] for m, (v, t) in enumerate(zip(values, tips, strict=True), start=1)],
                hovertemplate=name + ": <b>%{customdata[0]}</b><extra></extra>",
            )
        )
    layout = _layout(
        barmode="group",
        bargap=0.22,
        bargroupgap=0.06,
        hovermode="x unified",
        uniformtext={"minsize": 9, "mode": "hide"},  # rótulo só quando cabe na coluna
        showlegend=True,
        legend=LEGEND,
        # sem eixo de valores: cada coluna traz o rótulo; folga no topo para o rótulo de fora da coluna
        yaxis={"visible": False, "range": [0, (max(everything + [0.0]) or 1) * 1.15], "fixedrange": True},
        xaxis={"showgrid": False, "automargin": True, "fixedrange": True},
        margin={"l": 8, "r": 8, "t": 36, "b": 8},
    )
    return _fig(data, layout) | {"meta": {"tooltip": "unified"}}


def fig_monthly_total(o: dict) -> dict:
    """Coluna "Total" ao lado do comparativo mensal: a soma de cada série no período, com escala própria (no mesmo
    gráfico o total esmagaria os meses). O orçamento do ano do ciclo inclui o CAPEX sem cronograma mensal, como o
    card. Mesmas margens do comparativo, para a base das barras alinhar. customdata = [valor, linhas do tooltip]."""
    lb = _labels(o)
    series = []
    if o["has_prev"]:
        series.append(("prev", lb["prev_series"], ANTERIOR))
    if o["has_actual"]:
        series.append(("ref", lb["actual"], REALIZADO))
    if o["has_budget"]:
        series.append(("budget", lb["budget"], ORCADO))
    totals = {key: sum(_num(r[key]) for r in o["monthly"]) for key, _, _ in series}
    unscheduled = _num(o["kpis"].get("budget_unscheduled"))
    if "budget" in totals:
        totals["budget"] += unscheduled
    data = []
    for key, name, color in series:
        v = totals[key]
        lines = [[f"{name}: {fmt_money(v)}", color]]
        if key == "ref" and v:
            prev, budget = totals.get("prev", 0.0), totals.get("budget", 0.0)
            if prev:
                lines.append([f"Variação: {fmt_pct((v - prev) / prev)} ({_signed_money(v - prev)})", ""])
            if budget:
                lines.append(
                    [f"Realizado vs orçado: {fmt_pct((v - budget) / budget)} ({_signed_money(v - budget)})", ""]
                )
        if key == "budget" and unscheduled:
            lines.append([f"inclui {fmt_money(unscheduled)} de CAPEX sem cronograma mensal", ""])
        data.append(
            go.Bar(
                x=["TOTAL"],
                y=[v],
                name=name,
                marker={"color": color, "cornerradius": 4},
                text=[_short(v) if v > 0 else ""],
                textposition="outside",
                cliponaxis=False,
                textfont={"size": 13, "color": INK[color]},
                customdata=[[fmt_money(v), lines]],
                hovertemplate=name + ": <b>%{customdata[0]}</b><extra></extra>",
            )
        )
    top = max(list(totals.values()) + [0.0])
    layout = _layout(
        barmode="group",
        bargap=0.3,
        bargroupgap=0.12,
        hovermode="x unified",
        showlegend=False,
        yaxis={"visible": False, "range": [0, (top or 1) * 1.15], "fixedrange": True},
        xaxis={"showgrid": False, "automargin": True, "fixedrange": True},
        margin={"l": 8, "r": 8, "t": 36, "b": 8},
    )
    return _fig(data, layout) | {"meta": {"tooltip": "unified"}}


def fig_cumulative(o: dict) -> dict:
    """Total acumulado: linha pontilhada do realizado (até o último mês com valor) sobre a área da base (orçado ou
    ano anterior, até o último mês com valor). Em cada mês, o rótulo da série mais alta fica acima do ponto e o da
    mais baixa, abaixo. customdata = [valor, mês (o clique filtra), linhas do tooltip]."""
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
    base_tip = lb["tip"]["budget" if base_key == "budget" else "prev"]
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
                textfont={"size": 12, "color": INK[base_color]},
                cliponaxis=False,
                customdata=[
                    [fmt_money(v), m, [[f"{base_tip}: {fmt_money(v)}", base_color]]] for m, v in enumerate(base, 1)
                ],
                hovertemplate=base_name + ": <b>%{customdata[0]}</b><extra></extra>",
            )
        )
    if ref:
        pos = [
            "top center" if (not base or i >= len(base) or v >= base[i]) else "bottom center" for i, v in enumerate(ref)
        ]
        tips = []
        for i, v in enumerate(ref):
            lines = [[f"{lb['tip']['ref']}: {fmt_money(v)}", REALIZADO]]
            if i < len(base) and base[i]:
                lines.append([f"Diferença: {fmt_money(v - base[i])} ({fmt_pct((v - base[i]) / base[i])})", ""])
            tips.append(lines)
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
                textfont={"size": 12, "color": INK[REALIZADO]},
                cliponaxis=False,
                customdata=[[fmt_money(v), m, t] for m, (v, t) in enumerate(zip(ref, tips, strict=True), start=1)],
                hovertemplate=lb["actual"] + ": <b>%{customdata[0]}</b><extra></extra>",
            )
        )
    layout = _layout(
        hovermode="x unified",
        showlegend=True,
        legend=LEGEND,
        # sem eixo de valores: os pontos trazem o rótulo; folga no topo para o rótulo acima do último ponto
        yaxis={"visible": False, "range": [0, (max(base + ref + [0.0]) or 1) * 1.12], "fixedrange": True},
        xaxis={
            "showgrid": False,
            "automargin": True,
            "fixedrange": True,
            "categoryorder": "array",
            "categoryarray": list(MONTHS),
        },
        margin={"l": 28, "r": 28, "t": 36, "b": 8},
    )
    return _fig(data, layout) | {"meta": {"tooltip": "unified"}}


def fig_rank(rows: list[dict], lb: dict, show_base: bool, selected: int | None = None) -> dict:
    """Barras horizontais do maior para o menor, em cor sólida; com base, barra fina abaixo de cada barra e a variação
    % no texto. As barras usam a largura útil inteira (valor e variação ficam na margem direita).
    customdata = [valor, base, variação, id (o clique filtra), linhas do tooltip]; a barra fina usa o mesmo."""
    rows = [r for r in rows if _num(r["value"]) or _num(r.get("base"))]
    cats = [_category(r["name"], r.get("code")) for r in rows]
    values = [_num(r["value"]) for r in rows]
    bases = [_num(r.get("base")) for r in rows]
    variations = [
        f"{fmt_pct(r['var_pct'])} ({_signed_money(v - b)})" if r.get("var_pct") is not None else "—"
        for r, v, b in zip(rows, values, bases, strict=True)
    ]
    opacity = [1.0 if (selected is None or r.get("id") == selected) else 0.3 for r in rows]

    def text(value: float, var) -> str:
        out = f"<b>{fmt_compact(value)}</b>"
        if show_base and var is not None:
            n = float(var)
            color = TXT_UP if n > 0 else TXT_DOWN if n < 0 else TXT_FLAT
            out += f"  <span style='color:{color};font-size:11px'>{fmt_pct(n)}</span>"
        return out

    def tip(value: float, base: float, var: str) -> list:
        lines = [[f"{lb['main']}: {fmt_money(value)}", lb["main_color"]]]
        if show_base:
            lines += [[f"{lb['base']}: {fmt_money(base)}", lb["base_color"]], [f"Variação: {var}", ""]]
        return lines

    customdata = [
        [fmt_money(v), fmt_money(b), var, r.get("id"), tip(v, b, var)]
        for v, b, var, r in zip(values, bases, variations, rows, strict=True)
    ]
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
            marker={"color": lb["main_color"], "cornerradius": 4, "opacity": opacity},
            text=[text(v, r.get("var_pct")) for v, r in zip(values, rows, strict=True)],
            textposition="outside",
            cliponaxis=False,
            textfont={"size": 12},
            customdata=customdata,
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
                marker={"color": lb["base_color"], "cornerradius": 3, "opacity": opacity},
                customdata=customdata,
                hovertemplate=hover + "<extra></extra>",
            )
        )
    top = max([*values, *bases, 0.0])
    layout = _layout(
        barmode="overlay",
        showlegend=False,
        height=max(120, 44 * len(rows) + 30),
        xaxis={
            "showticklabels": False,
            "showgrid": False,
            "zeroline": False,
            "range": [0, top * 1.02 or 1],
            "fixedrange": True,
        },
        yaxis={"autorange": "reversed", "automargin": True, "tickfont": {"size": 12}, "fixedrange": True},
        margin={"l": 8, "r": 150 if show_base else 96, "t": 8, "b": 8},
    )
    return _fig(data, layout) | {"meta": {"tooltip": "point"}}


def _unit(top: float) -> tuple[float, str, int]:
    """Uma escala só para todos os rótulos da cascata (como no slide: tudo em R$ mi com 1 casa)."""
    for div, unit in ((1e9, " bi"), (1e6, " mi"), (1e3, " mil")):
        if top >= div:
            return div, unit, 1
    return 1.0, "", 0


def fig_evolution(milestones: list[dict]) -> dict | None:
    """Evolução do orçamento (cascata, no modelo do slide da diretoria): colunas cheias para os marcos (azul =
    realizado, verde-água = orçado/orçamento) e, entre cada par, uma barra flutuante de variação (areia) com o valor
    (redução entre parênteses) e o % num balão acima; linhas tracejadas ligam o topo de cada barra à seguinte e uma
    seta por cima leva do primeiro ao último marco com a variação total. Eixo x numérico (marcos nas posições pares,
    variações nas ímpares), para a variação não ocupar uma coluna inteira no celular.
    customdata = [título do tooltip, valor por extenso, linhas do tooltip]."""
    if len(milestones) < 2:
        return None
    values = [_num(m["value"]) for m in milestones]
    labels = [m["label"] for m in milestones]
    div, unit, decimals = _unit(max(values))

    def amount(v: float) -> str:
        return f"{_br(abs(v) / div, decimals)}{unit}"

    xs = [2 * i for i in range(len(milestones))]
    tips = []
    for i, (m, v) in enumerate(zip(milestones, values, strict=True)):
        color = REALIZADO if m["kind"] == "actual" else ORCADO
        lines = [[f"{m['label']}: {fmt_money(v)}", color]]
        if m.get("ytd"):
            lines.append([f"Realizado até {m['closed_month']}: {fmt_money(_num(m['ytd']))} (× 12/{m['closed']})", ""])
        if _num(m.get("unscheduled")):
            lines.append([f"inclui {fmt_money(_num(m['unscheduled']))} de CAPEX sem cronograma mensal", ""])
        if i:
            prev = values[i - 1]
            lines.append([f"vs {labels[i - 1]}: {fmt_pct((v - prev) / prev)} ({_signed_money(v - prev)})", ""])
        tips.append(lines)

    data = []
    for kind, name, color in (("actual", "Realizado", REALIZADO), ("budget", "Orçado", ORCADO)):
        idx = [i for i, m in enumerate(milestones) if m["kind"] == kind]
        if not idx:
            continue
        data.append(
            go.Bar(
                x=[xs[i] for i in idx],
                y=[values[i] for i in idx],
                name=name,
                width=0.8,
                marker={"color": color, "cornerradius": 4},
                text=[f"R$ {amount(values[i])}" for i in idx],
                textposition="outside",
                cliponaxis=False,
                textfont={"size": 12, "color": INK[color]},
                customdata=[[labels[i], fmt_money(values[i]), tips[i]] for i in idx],
                hovertemplate="%{customdata[0]}: <b>%{customdata[1]}</b><extra></extra>",
            )
        )

    # altura útil aproximada do gráfico (400 px menos legenda e rótulos do eixo), para folgas em pixels no eixo y
    top = max(values)
    plot_px = 300.0
    y_max = top * plot_px / (plot_px - 100)
    px = y_max / plot_px
    shapes: list[dict] = []
    annotations: list[dict] = []
    var_x, var_y, var_base, var_text, var_cd = [], [], [], [], []
    for i in range(1, len(milestones)):
        a, b = values[i - 1], values[i]
        diff, x = b - a, xs[i] - 1
        pct = diff / a if a else None
        var_x.append(x)
        var_y.append(abs(diff))
        var_base.append(min(a, b))
        var_text.append(f"+{amount(diff)}" if diff > 0 else f"({amount(diff)})" if diff < 0 else amount(0))
        var_cd.append(
            [
                f"{labels[i - 1]} → {labels[i]}",
                _signed_money(diff),
                [
                    [f"Variação: {fmt_pct(pct)} ({_signed_money(diff)})", VARIACAO],
                    [f"De {fmt_money(a)} para {fmt_money(b)}", ""],
                ],
            ]
        )
        # balão com o % acima da barra de variação (acima do rótulo do valor)
        annotations.append(
            {
                "x": x,
                "y": max(a, b),
                "xref": "x",
                "yref": "y",
                "yanchor": "bottom",
                "yshift": 22,
                "text": fmt_pct(pct),
                "showarrow": False,
                "font": {"size": 11, "color": TXT_UP if diff > 0 else TXT_DOWN if diff < 0 else TXT_FLAT},
                "bordercolor": VARIACAO,
                "borderwidth": 1,
                "borderpad": 3,
            }
        )
        # tracejado: topo do marco → início da variação → topo do marco seguinte
        for y, x0, x1 in ((a, xs[i - 1] + 0.4, x - 0.3), (b, x + 0.3, xs[i] - 0.4)):
            shapes.append(
                {
                    "type": "line",
                    "xref": "x",
                    "yref": "y",
                    "x0": x0,
                    "x1": x1,
                    "y0": y,
                    "y1": y,
                    "line": {"color": GUIDE, "width": 1, "dash": "dash"},
                }
            )
    data.append(
        go.Bar(
            x=var_x,
            y=var_y,
            base=var_base,
            name="Aumento / Redução",
            width=0.6,
            marker={"color": VARIACAO, "cornerradius": 3},
            text=var_text,
            textposition="outside",
            cliponaxis=False,
            textfont={"size": 12, "color": INK[VARIACAO]},
            customdata=var_cd,
            hovertemplate="%{customdata[0]}: <b>%{customdata[1]}</b><extra></extra>",
        )
    )

    # seta do primeiro ao último marco, por cima de tudo, com a variação total
    first, last = values[0], values[-1]
    total = last - first
    total_pct = total / first if first else None
    line_y = top + 74 * px
    shapes.append(
        {
            "type": "path",
            "xref": "x",
            "yref": "y",
            "path": f"M {xs[0]},{first + 30 * px} L {xs[0]},{line_y} L {xs[-1]},{line_y}",
            "line": {"color": GUIDE, "width": 1.5},
        }
    )
    sign = "-" if total < 0 else "+" if total > 0 else ""
    annotations += [
        {
            "x": xs[-1],
            "y": last + 30 * px,
            "ax": xs[-1],
            "ay": line_y,
            "xref": "x",
            "yref": "y",
            "axref": "x",
            "ayref": "y",
            "text": "",
            "showarrow": True,
            "arrowhead": 2,
            "arrowsize": 1.2,
            "arrowwidth": 1.5,
            "arrowcolor": GUIDE,
        },
        {
            "x": (xs[0] + xs[-1]) / 2,
            "y": line_y,
            "xref": "x",
            "yref": "y",
            "yanchor": "bottom",
            "yshift": 2,
            "showarrow": False,
            "text": f"Variação total: <b>{sign}R$ {amount(total)} ({fmt_pct(total_pct)})</b>",
            "font": {"size": 12, "color": TXT_UP if total > 0 else TXT_DOWN if total < 0 else TXT_FLAT},
        },
    ]
    ticks = [f"{m['word']}<br>{m['year']}" + (f"<br>({m['note']})" if m.get("note") else "") for m in milestones]
    layout = _layout(
        barmode="overlay",
        shapes=shapes,
        annotations=annotations,
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "x": 0,
            "font": {"size": 12},
            "itemclick": False,
            "itemdoubleclick": False,
        },
        xaxis={
            "tickmode": "array",
            "tickvals": xs,
            "ticktext": ticks,
            "range": [-0.7, xs[-1] + 0.7],
            "showgrid": False,
            "zeroline": False,
            "fixedrange": True,
            "automargin": True,
        },
        yaxis={"visible": False, "range": [0, y_max], "fixedrange": True},
        margin={"l": 8, "r": 8, "t": 36, "b": 8},
    )
    meta = {
        "tooltip": "point",
        "tooltip_title": "customdata",
        "first": labels[0],
        "last": labels[-1],
        "total_pct": fmt_pct(total_pct),
        "total_diff": _signed_money(total),
    }
    return _fig(data, layout) | {"meta": meta}


def build(
    o: dict,
    monthly: list[dict] | None = None,
    top_cost_centers: list[dict] | None = None,
    top_accounts: list[dict] | None = None,
    selected: dict | None = None,
    evolution: list[dict] | None = None,
) -> dict:
    """Todas as figuras do Painel 2 a partir da resposta de `/dashboard/overview`. `monthly`, `top_cost_centers` e
    `top_accounts` podem vir sem o filtro do próprio visual (mês, CC, conta): aí o visual mostra todos os itens e
    `selected` diz quais destacar."""
    selected = selected or {}
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
        "monthly": fig_monthly(o, monthly, selected.get("months")),
        "monthly_total": fig_monthly_total(o),
        "cumulative": fig_cumulative(o),
        "top_cost_centers": fig_rank(
            ranked(o["top_cost_centers"] if top_cost_centers is None else top_cost_centers),
            lb,
            show_base,
            selected.get("cost_center_id"),
        ),
        "top_accounts": fig_rank(
            ranked(o["top_accounts"] if top_accounts is None else top_accounts),
            lb,
            show_base,
            selected.get("account_id"),
        ),
    }
    evolution_fig = fig_evolution(evolution or [])
    if evolution_fig:
        figures["evolution"] = evolution_fig
    return figures
