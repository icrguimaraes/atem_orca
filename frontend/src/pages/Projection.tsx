import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { FilterBar } from "../components/FilterBar";
import { Alert, Card, Empty, Loading, PageHeader, SearchBox, Stat, lines, useLoad } from "../components/ui";
import { MONTHS, fmtDateTime, fmtMoney, fmtPct } from "../labels";
import { usePersistentState } from "../persist";

/* Projeção do gestor (05/11/2026): o gestor digita a projeção dos meses que faltam, conta × mês, para OPEX, Pessoal e
   CAPEX. Esta é a lista de centros de custo; a grade fica em ProjectionGrid. */

export interface LastSaved { at: string; by: string | null }

interface CcItem {
  id: number; code: string; name: string; company: string; closed: number;
  actual: string; projection: string; closing: string; budget: string; deviation: string; deviation_pct: string | null;
  last_saved: LastSaved | null;
}

interface CcList { year: number; items: CcItem[]; closed_by_company: Record<string, number> }

export const monthName = (m: number) => (m >= 1 && m <= 12 ? MONTHS[m - 1].charAt(0) + MONTHS[m - 1].slice(1).toLowerCase() : "—");

export default function Projection() {
  const navigate = useNavigate();
  const { data, error } = useLoad(() => api<CcList>("/projection/cost-centers"), []);
  const [company, setCompany] = usePersistentState("projecao.empresa", "");
  const [status, setStatus] = usePersistentState("projecao.status", "");
  const [query, setQuery] = useState("");

  const items = useMemo(() => {
    const q = query.trim().toUpperCase();
    return (data?.items ?? []).filter((i) =>
      (!company || i.company === company) &&
      (!status || (status === "saved" ? !!i.last_saved : !i.last_saved)) &&
      (!q || `${i.code} ${i.name}`.toUpperCase().includes(q)),
    );
  }, [data, company, status, query]);

  if (error) return <Alert>{error}</Alert>;
  if (!data) return <Loading />;
  const sum = (k: keyof CcItem) => items.reduce((s, i) => s + Number(i[k] as string), 0);
  const companies = Object.keys(data.closed_by_company).sort();
  const closedText = companies.map((c) => `${c}: até ${monthName(data.closed_by_company[c])}`).join(" · ");

  return (
    <>
      <PageHeader
        title={`Projeção ${data.year}`}
        subtitle={`Digite a projeção dos meses que faltam, conta a conta. Realizado fechado — ${closedText || "sem realizado carregado"}.`}
      />
      <FilterBar
        fields={[
          { key: "company", label: "Empresa", value: company, onChange: setCompany, options: [{ value: "", label: "Todas as empresas" }, ...companies.map((c) => ({ value: c, label: c }))] },
          { key: "status", label: "Gravação", value: status, onChange: setStatus, options: [{ value: "", label: "Todos" }, { value: "saved", label: "Já gravados na tela" }, { value: "never", label: "Nunca gravados" }] },
        ]}
        onReset={() => { setCompany(""); setStatus(""); setQuery(""); }}
        resetCount={(company ? 1 : 0) + (status ? 1 : 0) + (query ? 1 : 0)}
        extra={<SearchBox value={query} onChange={setQuery} placeholder="Buscar centro de custo" />}
      />
      <div className="stats">
        <Stat label="Realizado" value={fmtMoney(sum("actual"))} tone="info" hint="meses fechados" />
        <Stat label="Projeção meses abertos" value={fmtMoney(sum("projection"))} />
        <Stat label={`Projeção ${data.year}`} value={fmtMoney(sum("closing"))} hint="realizado + projeção dos meses abertos" />
        <Stat label={`Orçado ${data.year}`} value={fmtMoney(sum("budget"))} tone="budget" hint={lines(`desvio ${fmtMoney(sum("closing") - sum("budget"))}`)} />
      </div>
      <Card title={`Centros de custo (${items.length})`}>
        {items.length === 0 ? (
          <Empty>Nenhum centro de custo para os filtros.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Centro de custo</th>
                  <th className="right">Realizado</th>
                  <th className="right">Projeção meses abertos</th>
                  <th className="right">Projeção {data.year}</th>
                  <th className="right">Orçado</th>
                  <th className="right">Desvio</th>
                  <th>Última gravação</th>
                </tr>
              </thead>
              <tbody>
                {items.map((i) => {
                  const dev = Number(i.deviation);
                  return (
                    <tr key={i.id} className="clickable" onClick={() => navigate(`/projecao/${i.id}`)}>
                      <td>
                        <Link to={`/projecao/${i.id}`} onClick={(e) => e.stopPropagation()}><strong>{i.code}</strong> · {i.name}</Link>
                        <div className="muted small">Empresa {i.company} · fechado até {monthName(i.closed)}</div>
                      </td>
                      <td className="right cell-actual">{fmtMoney(i.actual)}</td>
                      <td className="right">{fmtMoney(i.projection)}</td>
                      <td className="right"><strong>{fmtMoney(i.closing)}</strong></td>
                      <td className="right cell-budget">{fmtMoney(i.budget)}</td>
                      <td className={`right ${dev > 0.005 ? "error-text" : ""}`}>
                        {fmtMoney(dev)}
                        {i.deviation_pct !== null && <div className="muted small">{fmtPct(String(Number(i.deviation_pct) / 100))}</div>}
                      </td>
                      <td className="small">{i.last_saved ? <>{fmtDateTime(i.last_saved.at)}<div className="muted">{i.last_saved.by}</div></> : <span className="muted">—</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
