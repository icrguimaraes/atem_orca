import { useMemo, useState } from "react";
import { api, type Account, type Branch, type Company, type CostCenter, type Package } from "../api";
import { Badge, Card, Empty, Loading, PageHeader, SearchBox, useLoad } from "../components/ui";
import { NATURE_LABELS, fmtInt } from "../labels";

type Tab = "cc" | "accounts" | "packages" | "branches" | "companies";

const TABS: { key: Tab; label: string }[] = [
  { key: "cc", label: "Centros de custo" },
  { key: "accounts", label: "Contas contábeis" },
  { key: "packages", label: "Pacotes GMD" },
  { key: "branches", label: "Filiais" },
  { key: "companies", label: "Empresas" },
];

const matches = (q: string, ...fields: (string | null | undefined)[]) =>
  !q || fields.some((f) => f?.toLowerCase().includes(q.toLowerCase()));

export default function MasterData() {
  const [tab, setTab] = useState<Tab>("cc");
  const [q, setQ] = useState("");
  const { data } = useLoad(async () => {
    const [companies, branches, ccs, accounts, packages] = await Promise.all([
      api<Company[]>("/companies"),
      api<Branch[]>("/branches"),
      api<CostCenter[]>("/cost-centers"),
      api<Account[]>("/accounts"),
      api<Package[]>("/packages"),
    ]);
    return { companies, branches, ccs, accounts, packages };
  });

  const maps = useMemo(
    () => ({
      company: new Map(data?.companies.map((c) => [c.id, c]) ?? []),
      pkg: new Map(data?.packages.map((p) => [p.id, p]) ?? []),
    }),
    [data],
  );

  if (!data) return <Loading />;
  const counts: Record<Tab, number> = {
    cc: data.ccs.length,
    accounts: data.accounts.length,
    packages: data.packages.length,
    branches: data.branches.length,
    companies: data.companies.length,
  };

  return (
    <>
      <PageHeader
        title="Cadastros"
        subtitle="Base mestre do orçamento. Atualizações em massa entram pela Importação de dados (aba BD-Novo ou exportação SAP)."
      />
      <div className="tabs">
        {TABS.map((t) => (
          <button key={t.key} className={tab === t.key ? "active" : ""} onClick={() => setTab(t.key)}>
            {t.label} <span className="count">{fmtInt(counts[t.key])}</span>
          </button>
        ))}
      </div>
      <Card actions={<SearchBox value={q} onChange={setQ} placeholder="Buscar por código ou nome" />}>
        <div className="table-wrap">
          {tab === "cc" && (
            <Table
              head={["Empresa", "Código", "Centro de custo", "Gestor", "Situação"]}
              rows={data.ccs
                .filter((c) => matches(q, c.code, c.name, c.manager_name))
                .map((c) => [
                  maps.company.get(c.company_id)?.short_name ?? c.company_id,
                  <span className="mono">{c.code}</span>,
                  c.name,
                  c.manager_name ?? <span className="muted">sem gestor</span>,
                  c.is_active ? <Badge tone="good">Ativo</Badge> : <Badge tone="neutral">Inativo</Badge>,
                ])}
            />
          )}
          {tab === "accounts" && (
            <Table
              head={["Conta", "Descrição", "Pacote GMD", "Natureza", "Agrupamento DRE"]}
              rows={data.accounts
                .filter((a) => matches(q, a.code, a.name, maps.pkg.get(a.package_id ?? 0)?.name))
                .map((a) => [
                  <span className="mono">{a.code}</span>,
                  a.name,
                  maps.pkg.get(a.package_id ?? 0)?.name ?? "—",
                  NATURE_LABELS[a.nature] ?? a.nature,
                  a.dre_group ?? "—",
                ])}
            />
          )}
          {tab === "packages" && (
            <Table
              head={["", "Pacote", "Tipo de validação", "Natureza", "Contas"]}
              rows={data.packages
                .filter((p) => matches(q, p.name, p.code))
                .map((p) => [
                  <span className="muted">{p.roman ?? ""}</span>,
                  p.name,
                  p.package_type === 1 ? (
                    <Badge tone="warn">Tipo 1 · validação obrigatória</Badge>
                  ) : (
                    <Badge tone="neutral">Tipo 2 · consultivo</Badge>
                  ),
                  NATURE_LABELS[p.nature] ?? p.nature,
                  fmtInt(data.accounts.filter((a) => a.package_id === p.id).length),
                ])}
            />
          )}
          {tab === "branches" && (
            <Table
              head={["Empresa", "Código", "Filial", "UF"]}
              rows={data.branches
                .filter((b) => matches(q, b.code, b.name))
                .map((b) => [maps.company.get(b.company_id)?.short_name ?? "", <span className="mono">{b.code}</span>, b.name, b.uf ?? "—"])}
            />
          )}
          {tab === "companies" && (
            <Table
              head={["Código SAP", "Empresa", "Situação"]}
              rows={data.companies
                .filter((c) => matches(q, c.code, c.name))
                .map((c) => [
                  <span className="mono">{c.code}</span>,
                  c.name,
                  c.is_active ? <Badge tone="good">Ativa</Badge> : <Badge tone="neutral">Inativa</Badge>,
                ])}
            />
          )}
        </div>
      </Card>
    </>
  );
}

function Table({ head, rows }: { head: string[]; rows: React.ReactNode[][] }) {
  if (!rows.length) return <Empty>Nenhum registro encontrado.</Empty>;
  return (
    <table className="table">
      <thead>
        <tr>
          {head.map((h, i) => (
            <th key={i}>{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            {r.map((c, j) => (
              <td key={j}>{c}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
