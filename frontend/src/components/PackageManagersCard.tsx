import { useState } from "react";
import { api, type Company, type Package, type User } from "../api";
import { Alert, Badge, Card, Loading, useLoad } from "./ui";

interface PM { id: number; cycle_id: number; package_id: number; company_id: number | null; user_id: number | null; manager_name: string; scope_label: string | null }

/** Vincula os gestores de pacote (Cartilha GMD) a usuários do sistema, para que validem os pacotes Tipo 1. */
export function PackageManagersCard({ cycleId, editable }: { cycleId: number; editable: boolean }) {
  const [error, setError] = useState<string | null>(null);
  const { data, reload } = useLoad(async () => {
    const [pms, packages, users, companies] = await Promise.all([
      api<PM[]>(`/package-managers?cycle_id=${cycleId}`),
      api<Package[]>("/packages"),
      editable ? api<User[]>("/users") : Promise.resolve([] as User[]),
      api<Company[]>("/companies"),
    ]);
    return { pms, packages: new Map(packages.map((p) => [p.id, p])), users, companies: new Map(companies.map((c) => [c.id, c])) };
  }, [cycleId, editable]);

  if (!data) return <Loading />;
  const sorted = [...data.pms].sort((a, b) => (data.packages.get(a.package_id)?.sort_order ?? 0) - (data.packages.get(b.package_id)?.sort_order ?? 0));

  async function link(pm: PM, userId: string) {
    setError(null);
    try {
      await api(`/package-managers/${pm.id}`, { method: "PATCH", body: JSON.stringify({ user_id: userId ? Number(userId) : null }) });
      reload();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <Card title="Gestores de pacote (GMD)">
      <p className="muted small">
        Vincule cada gestor a um usuário com perfil “Gestor de pacote”. Nos pacotes Tipo 1, o orçamento só pode ser aprovado depois da validação dele.
      </p>
      {error && <Alert>{error}</Alert>}
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Pacote</th><th>Tipo</th><th>Gestor (cartilha)</th><th>Escopo</th><th>Usuário no sistema</th></tr></thead>
          <tbody>
            {sorted.map((pm) => {
              const p = data.packages.get(pm.package_id);
              return (
                <tr key={pm.id}>
                  <td>{p?.roman ? `${p.roman} · ` : ""}{p?.name}</td>
                  <td>{p?.package_type === 1 ? <Badge tone="warn">Tipo 1</Badge> : <Badge tone="neutral">Tipo 2</Badge>}</td>
                  <td>{pm.manager_name}</td>
                  <td className="muted">{[pm.scope_label, pm.company_id ? data.companies.get(pm.company_id)?.short_name : null].filter(Boolean).join(" · ") || "Todas as empresas"}</td>
                  <td>
                    {editable ? (
                      <select value={pm.user_id ?? ""} onChange={(e) => link(pm, e.target.value)}>
                        <option value="">— não vinculado —</option>
                        {data.users.filter((u) => u.is_active).map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                      </select>
                    ) : pm.user_id ? "vinculado" : <Badge tone="warn">não vinculado</Badge>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
