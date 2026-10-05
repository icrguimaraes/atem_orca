import { useMemo, useState, type ReactNode } from "react";
import { api, type Account, type Branch, type Company, type CostCenter, type Package, type User } from "../api";
import { useAuth } from "../auth";
import { RecordForm, type Field } from "../components/RecordForm";
import { Badge, Card, Empty, Loading, PageHeader, SearchBox, useLoad } from "../components/ui";
import { NATURE_LABELS, fmtInt } from "../labels";

type Tab = "cc" | "accounts" | "packages" | "assets" | "branches" | "companies";

interface AssetClassRow { id: number; name: string; account_id: number | null }
interface AssetItemRow { id: number; name: string; asset_class_id: number }

const TABS: { key: Tab; label: string }[] = [
  { key: "cc", label: "Centros de custo" },
  { key: "accounts", label: "Contas contábeis" },
  { key: "packages", label: "Pacotes GMD" },
  { key: "assets", label: "Catálogo de ativos" },
  { key: "branches", label: "Filiais" },
  { key: "companies", label: "Empresas" },
];

const NATURES = Object.entries(NATURE_LABELS).map(([value, label]) => ({ value, label }));

const matches = (q: string, ...fields: (string | null | undefined)[]) =>
  !q || fields.some((f) => f?.toLowerCase().includes(q.toLowerCase()));

const active = (v: boolean) => (v ? <Badge tone="good">Ativo</Badge> : <Badge tone="neutral">Inativo</Badge>);

interface Editing {
  title: string;
  endpoint: string;
  id?: string | number;
  initial: object;
  fields: Field[];
}

export default function MasterData() {
  const { can } = useAuth();
  const isAdmin = can();
  const [tab, setTab] = useState<Tab>("cc");
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<Editing | null>(null);
  const { data, reload } = useLoad(async () => {
    const [companies, branches, ccs, accounts, packages, users, assetClasses, assetItems] = await Promise.all([
      api<Company[]>("/companies"),
      api<Branch[]>("/branches"),
      api<CostCenter[]>("/cost-centers"),
      api<Account[]>("/accounts?include_inactive=true"),
      api<Package[]>("/packages"),
      isAdmin ? api<User[]>("/users") : Promise.resolve([] as User[]),
      api<AssetClassRow[]>("/asset-classes"),
      api<AssetItemRow[]>("/asset-items"),
    ]);
    return { companies, branches, ccs, accounts, packages, users, assetClasses, assetItems };
  }, [isAdmin]);

  const maps = useMemo(
    () => ({
      company: new Map(data?.companies.map((c) => [c.id, c]) ?? []),
      pkg: new Map(data?.packages.map((p) => [p.id, p]) ?? []),
      user: new Map(data?.users.map((u) => [u.id, u]) ?? []),
      assetClass: new Map(data?.assetClasses.map((c) => [c.id, c]) ?? []),
      account: new Map(data?.accounts.map((a) => [a.id, a]) ?? []),
    }),
    [data],
  );

  if (!data) return <Loading />;
  const companyOpts = data.companies.map((c) => ({ value: c.id, label: `${c.code} · ${c.name}` }));
  const packageOpts = data.packages.map((p) => ({ value: p.id, label: `${p.roman ? `${p.roman} · ` : ""}${p.name}` }));
  const userOpts = data.users.filter((u) => u.is_active).map((u) => ({ value: u.id, label: `${u.name} (${u.email})` }));

  const forms: Record<Tab, (row?: any) => Editing> = {
    cc: (r?: CostCenter) => ({
      title: r ? `Centro de custo ${r.code}` : "Novo centro de custo",
      endpoint: "/cost-centers",
      id: r?.id,
      initial: r ?? { company_id: data.companies[0]?.id, code: "", name: "", manager_user_id: null, manager_name: "", is_csc: false, is_backoffice: false, is_active: true },
      fields: [
        { key: "company_id", label: "Empresa", type: "select", options: companyOpts, required: true, readOnlyOnEdit: true },
        { key: "code", label: "Código SAP", type: "text", required: true, readOnlyOnEdit: true },
        { key: "name", label: "Denominação", type: "text", required: true },
        { key: "manager_user_id", label: "Usuário gestor (quem enxerga e preenche o orçamento do CC)", type: "select", options: userOpts, allowEmpty: true },
        { key: "manager_name", label: "Nome do gestor (como vem do SAP)", type: "text" },
        { key: "is_csc", label: "CSC — atende várias empresas (orçado na ATEM)", type: "checkbox" },
        { key: "is_backoffice", label: "Centro de custo de BackOffice (recebe rateio)", type: "checkbox" },
        { key: "is_active", label: "Ativo", type: "checkbox" },
      ],
    }),
    accounts: (r?: Account) => ({
      title: r ? `Conta ${r.code}` : "Nova conta contábil",
      endpoint: "/accounts",
      id: r?.id,
      initial: r ?? { code: "", name: "", dre_group: "Despesas", nature: "OPEX", package_id: null, is_active: true },
      fields: [
        { key: "code", label: "Conta do razão", type: "text", required: true, readOnlyOnEdit: true },
        { key: "name", label: "Descrição", type: "text", required: true },
        { key: "package_id", label: "Pacote GMD", type: "select", options: packageOpts, allowEmpty: true },
        { key: "nature", label: "Natureza", type: "select", options: NATURES, required: true },
        { key: "dre_group", label: "Agrupamento DRE", type: "text" },
        { key: "is_active", label: "Ativa", type: "checkbox" },
      ],
    }),
    packages: (r?: Package) => ({
      title: r ? `Pacote ${r.name}` : "Novo pacote GMD",
      endpoint: "/packages",
      id: r?.id,
      initial: r ?? { code: "", name: "", roman: "", package_type: 2, nature: "OPEX", form_type: "GENERIC", sort_order: 99, is_active: true },
      fields: [
        { key: "code", label: "Código interno", type: "text", required: true, readOnlyOnEdit: true },
        { key: "name", label: "Nome", type: "text", required: true },
        { key: "roman", label: "Numeração (I, II…)", type: "text" },
        {
          key: "package_type", label: "Tipo de validação", type: "select", required: true,
          options: [{ value: 1, label: "Tipo 1 · validação obrigatória do gestor de pacote" }, { value: 2, label: "Tipo 2 · consultivo" }],
        },
        { key: "nature", label: "Natureza", type: "select", options: NATURES, required: true },
        { key: "sort_order", label: "Ordem de exibição", type: "number" },
        { key: "is_active", label: "Ativo", type: "checkbox" },
      ],
    }),
    assets: (r?: AssetItemRow) => ({
      title: r ? `Item ${r.name}` : "Novo item do catálogo de ativos",
      endpoint: "/asset-items",
      id: r?.id,
      initial: r ?? { name: "", asset_class_id: data.assetClasses[0]?.id },
      fields: [
        { key: "name", label: "Item principal", type: "text", required: true },
        {
          key: "asset_class_id", label: "Classe de ativo (define a conta sugerida)", type: "select", required: true,
          options: data.assetClasses.map((c) => ({ value: c.id, label: `${c.name}${c.account_id ? ` · ${maps.account.get(c.account_id)?.code ?? ""}` : ""}` })),
        },
      ],
    }),
    branches: (r?: Branch) => ({
      title: r ? `Filial ${r.code}` : "Nova filial",
      endpoint: "/branches",
      id: r?.id,
      initial: r ?? { company_id: data.companies[0]?.id, code: "", name: "", uf: "", is_active: true },
      fields: [
        { key: "company_id", label: "Empresa", type: "select", options: companyOpts, required: true, readOnlyOnEdit: true },
        { key: "code", label: "Local de negócios (código)", type: "text", required: true, readOnlyOnEdit: true },
        { key: "name", label: "Nome", type: "text", required: true },
        { key: "uf", label: "UF", type: "text" },
        { key: "is_active", label: "Ativa", type: "checkbox" },
      ],
    }),
    companies: (r?: Company) => ({
      title: r ? `Empresa ${r.code}` : "Nova empresa",
      endpoint: "/companies",
      id: r?.id,
      initial: r ?? { code: "", name: "", short_name: "", is_active: true },
      fields: [
        { key: "code", label: "Código SAP (4 dígitos)", type: "text", required: true, readOnlyOnEdit: true },
        { key: "name", label: "Razão / nome", type: "text", required: true },
        { key: "short_name", label: "Nome curto", type: "text" },
        { key: "is_active", label: "Ativa", type: "checkbox" },
      ],
    }),
  };

  const edit = (row: unknown) => isAdmin && setEditing(forms[tab](row));
  const editCell = (row: unknown): ReactNode =>
    isAdmin ? (
      <button className="btn btn-ghost btn-sm" onClick={() => edit(row)}>Editar</button>
    ) : null;

  const counts: Record<Tab, number> = {
    cc: data.ccs.length,
    accounts: data.accounts.length,
    packages: data.packages.length,
    assets: data.assetItems.length,
    branches: data.branches.length,
    companies: data.companies.length,
  };

  return (
    <>
      <PageHeader
        title="Cadastros"
        subtitle={
          isAdmin
            ? "Clique em Editar para alterar um registro. Para deixar de usar um cadastro, desmarque Ativo (o histórico é preservado). Toda alteração fica na auditoria."
            : "Base mestre do orçamento (somente leitura para o seu perfil)."
        }
        actions={isAdmin && <button className="btn btn-primary" onClick={() => setEditing(forms[tab]())}>Novo</button>}
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
              head={["Empresa", "Código", "Centro de custo", "Gestor (SAP)", "Usuário gestor", "Situação", ""]}
              rows={data.ccs
                .filter((c) => matches(q, c.code, c.name, c.manager_name))
                .map((c) => [
                  maps.company.get(c.company_id)?.short_name ?? c.company_id,
                  <span className="mono">{c.code}</span>,
                  c.name,
                  c.manager_name ?? <span className="muted">—</span>,
                  c.manager_user_id ? (maps.user.get(c.manager_user_id)?.name ?? `#${c.manager_user_id}`) : <Badge tone="warn">sem usuário</Badge>,
                  active(c.is_active),
                  editCell(c),
                ])}
            />
          )}
          {tab === "accounts" && (
            <Table
              head={["Conta", "Descrição", "Pacote GMD", "Natureza", "Agrupamento DRE", "Situação", ""]}
              rows={data.accounts
                .filter((a) => matches(q, a.code, a.name, maps.pkg.get(a.package_id ?? 0)?.name))
                .map((a) => [
                  <span className="mono">{a.code}</span>,
                  a.name,
                  maps.pkg.get(a.package_id ?? 0)?.name ?? <Badge tone="warn">sem pacote</Badge>,
                  NATURE_LABELS[a.nature] ?? a.nature,
                  a.dre_group ?? "—",
                  active(a.is_active),
                  editCell(a),
                ])}
            />
          )}
          {tab === "packages" && (
            <Table
              head={["", "Pacote", "Tipo de validação", "Natureza", "Contas", "Situação", ""]}
              rows={data.packages
                .filter((p) => matches(q, p.name, p.code))
                .map((p) => [
                  <span className="muted">{p.roman ?? ""}</span>,
                  p.name,
                  p.package_type === 1 ? <Badge tone="warn">Tipo 1 · validação obrigatória</Badge> : <Badge tone="neutral">Tipo 2 · consultivo</Badge>,
                  NATURE_LABELS[p.nature] ?? p.nature,
                  fmtInt(data.accounts.filter((a) => a.package_id === p.id).length),
                  active(p.is_active),
                  editCell(p),
                ])}
            />
          )}
          {tab === "assets" && (
            <Table
              head={["Item principal", "Classe de ativo", "Conta sugerida", ""]}
              rows={data.assetItems
                .filter((i) => matches(q, i.name, maps.assetClass.get(i.asset_class_id)?.name))
                .map((i) => {
                  const klass = maps.assetClass.get(i.asset_class_id);
                  const acc = klass?.account_id ? maps.account.get(klass.account_id) : undefined;
                  return [
                    i.name,
                    klass?.name ?? "—",
                    acc ? <><span className="mono">{acc.code}</span> · {acc.name}</> : <Badge tone="warn">sem conta</Badge>,
                    editCell(i),
                  ];
                })}
            />
          )}
          {tab === "branches" && (
            <Table
              head={["Empresa", "Código", "Filial", "UF", "Situação", ""]}
              rows={data.branches
                .filter((b) => matches(q, b.code, b.name))
                .map((b) => [
                  maps.company.get(b.company_id)?.short_name ?? "",
                  <span className="mono">{b.code}</span>,
                  b.name,
                  b.uf ?? "—",
                  active(b.is_active),
                  editCell(b),
                ])}
            />
          )}
          {tab === "companies" && (
            <Table
              head={["Código SAP", "Empresa", "Nome curto", "Situação", ""]}
              rows={data.companies
                .filter((c) => matches(q, c.code, c.name))
                .map((c) => [<span className="mono">{c.code}</span>, c.name, c.short_name ?? "—", active(c.is_active), editCell(c)])}
            />
          )}
        </div>
      </Card>
      {editing && <RecordForm {...editing} onClose={() => setEditing(null)} onSaved={reload} />}
    </>
  );
}

function Table({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
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
              <td key={j} className={j === r.length - 1 ? "row-actions" : undefined}>{c}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
