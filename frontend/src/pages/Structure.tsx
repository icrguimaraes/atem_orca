import { useMemo, useState } from "react";
import { api, type Company, type CostCenter, type Department, type Sector } from "../api";
import { useAuth } from "../auth";
import { RecordForm, type Field } from "../components/RecordForm";
import { Alert, Badge, Card, Empty, Loading, PageHeader, SearchBox, Stat, useLoad } from "../components/ui";
import { fmtInt } from "../labels";

interface Editing { title: string; endpoint: string; id?: number; initial: object; fields: Field[] }

/** Áreas e setores: a estrutura que o Painel usa na tabela (área → setor → pacote → conta).
 *  Cada centro de custo fica num setor; o setor pertence a uma área (no banco: setor = `areas`, área = `departments`). */
export default function Structure() {
  const { can } = useAuth();
  const isAdmin = can();
  const { data, error, reload } = useLoad(async () => {
    const [departments, sectors, ccs, companies] = await Promise.all([
      api<Department[]>("/departments"),
      api<Sector[]>("/areas"),
      api<CostCenter[]>("/cost-centers"),
      api<Company[]>("/companies"),
    ]);
    return { departments, sectors, ccs, companies };
  });
  const [editing, setEditing] = useState<Editing | null>(null);
  const [q, setQ] = useState("");
  const [onlyLoose, setOnlyLoose] = useState(false);
  const [msg, setMsg] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const [saving, setSaving] = useState<number | null>(null);

  const view = useMemo(() => {
    if (!data) return null;
    const deptName = new Map(data.departments.map((d) => [d.id, d.name]));
    const sectorById = new Map(data.sectors.map((s) => [s.id, s]));
    const ccsBySector = new Map<number, CostCenter[]>();
    for (const cc of data.ccs) {
      if (cc.area_id) ccsBySector.set(cc.area_id, [...(ccsBySector.get(cc.area_id) ?? []), cc]);
    }
    const groups = [...data.departments]
      .sort((a, b) => a.name.localeCompare(b.name, "pt-BR"))
      .map((d) => ({
        dept: d,
        sectors: data.sectors
          .filter((s) => s.department_id === d.id)
          .sort((a, b) => a.name.localeCompare(b.name, "pt-BR")),
      }));
    const orphanSectors = data.sectors.filter((s) => !s.department_id || !deptName.has(s.department_id));
    return { deptName, sectorById, ccsBySector, groups, orphanSectors };
  }, [data]);

  if (error) return <Alert>{error}</Alert>;
  if (!data || !view) return <Loading />;

  const companyName = new Map(data.companies.map((c) => [c.id, c.short_name ?? c.code]));
  const activeCcs = data.ccs.filter((c) => c.is_active);
  const loose = activeCcs.filter((c) => !c.area_id);
  const shown = activeCcs
    .filter((c) => !onlyLoose || !c.area_id)
    .filter((c) => !q || `${c.code} ${c.name}`.toLowerCase().includes(q.toLowerCase()));
  const deptOpts = data.departments.map((d) => ({ value: d.id, label: d.name }));

  const newDept = (): Editing => ({ title: "Nova área", endpoint: "/departments", initial: { name: "" }, fields: [{ key: "name", label: "Nome da área (ex.: Projeção)", type: "text", required: true }] });
  const editDept = (d: Department): Editing => ({ title: `Área ${d.name}`, endpoint: "/departments", id: d.id, initial: d, fields: [{ key: "name", label: "Nome da área", type: "text", required: true }] });
  const sectorForm = (s?: Sector, deptId?: number): Editing => ({
    title: s ? `Setor ${s.name}` : "Novo setor",
    endpoint: "/areas",
    id: s?.id,
    initial: s ?? { name: "", department_id: deptId ?? data.departments[0]?.id ?? null },
    fields: [
      { key: "name", label: "Nome do setor (ex.: Fiscal)", type: "text", required: true },
      { key: "department_id", label: "Área", type: "select", options: deptOpts, required: true },
    ],
  });

  async function assign(cc: CostCenter, sectorId: string) {
    const sector = sectorId ? view!.sectorById.get(Number(sectorId)) : undefined;
    setSaving(cc.id);
    setMsg(null);
    try {
      await api(`/cost-centers/${cc.id}`, {
        method: "PATCH",
        body: JSON.stringify({ area_id: sector?.id ?? null, department_id: sector?.department_id ?? null }),
      });
      setMsg({
        tone: "good",
        text: sector
          ? `${cc.code} · ${cc.name} → ${view!.deptName.get(sector.department_id ?? 0) ?? "—"} › ${sector.name}`
          : `${cc.code} · ${cc.name} ficou sem setor`,
      });
      reload();
    } catch (err) {
      setMsg({ tone: "bad", text: (err as Error).message });
    } finally {
      setSaving(null);
    }
  }

  return (
    <>
      <PageHeader
        title="Áreas e setores"
        subtitle="Estrutura usada na tabela do Painel (área → setor → pacote → conta). Cada centro de custo fica num setor, e cada setor pertence a uma área."
        actions={isAdmin && (
          <>
            <button className="btn" onClick={() => setEditing(newDept())}>Nova área</button>
            <button className="btn btn-primary" onClick={() => setEditing(sectorForm())} disabled={!data.departments.length}>Novo setor</button>
          </>
        )}
      />
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      <div className="stats">
        <Stat label="Áreas" value={fmtInt(data.departments.length)} />
        <Stat label="Setores" value={fmtInt(data.sectors.length)} />
        <Stat label="CCs com setor" value={fmtInt(activeCcs.length - loose.length)} hint={`de ${fmtInt(activeCcs.length)} ativos`} />
        <Stat label="CCs sem setor" value={fmtInt(loose.length)} tone={loose.length ? "warn" : "good"} hint="aparecem como “Sem área” no Painel" />
      </div>

      <div className="grid-2">
        {view.groups.map(({ dept, sectors }) => (
          <Card
            key={dept.id}
            title={dept.name}
            actions={isAdmin && (
              <>
                <button className="btn btn-ghost btn-sm" onClick={() => setEditing(editDept(dept))}>Renomear</button>
                <button className="btn btn-ghost btn-sm" onClick={() => setEditing(sectorForm(undefined, dept.id))}>+ Setor</button>
              </>
            )}
          >
            {sectors.length === 0 ? (
              <Empty>Nenhum setor nesta área.</Empty>
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Setor</th><th>Centros de custo</th>{isAdmin && <th />}</tr></thead>
                  <tbody>
                    {sectors.map((s) => {
                      const ccs = view.ccsBySector.get(s.id) ?? [];
                      return (
                        <tr key={s.id}>
                          <td><strong>{s.name}</strong></td>
                          <td>
                            {ccs.length === 0 ? <span className="muted small">nenhum</span> : ccs.map((c) => (
                              <div key={c.id} className="small"><span className="mono">{c.code}</span> · {c.name}</div>
                            ))}
                          </td>
                          {isAdmin && <td className="row-actions"><button className="btn btn-ghost btn-sm" onClick={() => setEditing(sectorForm(s))}>Editar</button></td>}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        ))}
        {view.orphanSectors.length > 0 && (
          <Card title="Setores sem área">
            {view.orphanSectors.map((s) => (
              <div key={s.id} className="small">
                {s.name}{isAdmin && <> · <button className="btn btn-ghost btn-sm" onClick={() => setEditing(sectorForm(s))}>Escolher área</button></>}
              </div>
            ))}
          </Card>
        )}
      </div>

      <Card
        title="Setor de cada centro de custo"
        actions={
          <div className="inline-controls">
            <label className="check small"><input type="checkbox" checked={onlyLoose} onChange={(e) => setOnlyLoose(e.target.checked)} /> Só sem setor</label>
            <SearchBox value={q} onChange={setQ} placeholder="Buscar por código ou nome" />
          </div>
        }
      >
        <p className="muted small">{isAdmin ? "Escolha o setor na lista: grava na hora e o Painel já usa. A área vem do setor." : "Somente leitura para o seu perfil."}</p>
        {shown.length === 0 ? (
          <Empty>Nenhum centro de custo com esses filtros.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Empresa</th><th>Código</th><th>Centro de custo</th><th>Área</th><th>Setor</th></tr></thead>
              <tbody>
                {shown.map((cc) => {
                  const sector = cc.area_id ? view.sectorById.get(cc.area_id) : undefined;
                  return (
                    <tr key={cc.id}>
                      <td>{companyName.get(cc.company_id) ?? "—"}</td>
                      <td className="mono">{cc.code}</td>
                      <td>{cc.name}</td>
                      <td>{sector ? view.deptName.get(sector.department_id ?? 0) ?? "—" : <Badge tone="warn">sem área</Badge>}</td>
                      <td>
                        {isAdmin ? (
                          <select value={cc.area_id ?? ""} disabled={saving === cc.id} onChange={(e) => assign(cc, e.target.value)} aria-label={`Setor de ${cc.code}`}>
                            <option value="">Sem setor</option>
                            {view.groups.map(({ dept, sectors }) => (
                              <optgroup key={dept.id} label={dept.name}>
                                {sectors.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                              </optgroup>
                            ))}
                          </select>
                        ) : (
                          sector?.name ?? <span className="muted">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {editing && <RecordForm {...editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload(); }} />}
    </>
  );
}
