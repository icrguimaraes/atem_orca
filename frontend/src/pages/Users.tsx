import { useState, type FormEvent } from "react";
import { api, type UserListItem } from "../api";
import { useAuth } from "../auth";
import { Alert, Badge, Card, Loading, PageHeader, useLoad } from "../components/ui";
import { AccessCell, UserAccessModal } from "../components/UserAccess";
import { ROLE_LABELS, fmtDateTime } from "../labels";

function NewUser({ onCreated }: { onCreated: () => void }) {
  const [form, setForm] = useState({ name: "", email: "", password: "", roles: ["MANAGER"] });
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  function toggle(role: string) {
    setForm((f) => ({ ...f, roles: f.roles.includes(role) ? f.roles.filter((r) => r !== role) : [...f.roles, role] }));
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setOk(null);
    try {
      await api("/users", { method: "POST", body: JSON.stringify(form) });
      setOk(`Usuário ${form.email} criado. Envie a senha inicial por um canal seguro.`);
      setForm({ name: "", email: "", password: "", roles: ["MANAGER"] });
      onCreated();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <form onSubmit={submit} className="stack">
      <div className="form-row">
        <label>
          Nome
          <input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </label>
        <label>
          E-mail
          <input type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        </label>
        <label>
          Senha inicial (mín. 8)
          <input type="text" required minLength={8} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        </label>
      </div>
      <div className="checks">
        {Object.entries(ROLE_LABELS).map(([code, label]) => (
          <label key={code} className="check">
            <input type="checkbox" checked={form.roles.includes(code)} onChange={() => toggle(code)} />
            {label}
          </label>
        ))}
      </div>
      {error && <Alert>{error}</Alert>}
      {ok && <Alert tone="good">{ok}</Alert>}
      <div>
        <button className="btn btn-primary" disabled={!form.roles.length}>Criar usuário</button>
      </div>
    </form>
  );
}

export default function Users() {
  const { can } = useAuth();
  const { data, error, reload } = useLoad(() => api<UserListItem[]>("/users"));
  const [editing, setEditing] = useState<UserListItem | null>(null);
  return (
    <>
      <PageHeader
        title="Usuários"
        subtitle="Cada usuário enxerga os centros de custo em que é gestor (cadastro do CC) e os atribuídos em Acessos; Administrador e Controladoria veem todos."
      />
      {can() && (
        <Card title="Novo usuário">
          <NewUser onCreated={reload} />
        </Card>
      )}
      <Card title="Usuários cadastrados">
        {error && <Alert>{error}</Alert>}
        {!data ? (
          <Loading />
        ) : (
          <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Nome</th>
                <th>E-mail</th>
                <th>Perfis</th>
                <th>Centros de custo</th>
                <th>Situação</th>
                <th>Último acesso</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.map((u) => (
                <tr key={u.id}>
                  <td>{u.name}</td>
                  <td>{u.email}</td>
                  <td>{u.roles.map((r) => ROLE_LABELS[r] ?? r).join(", ")}</td>
                  <td className="nowrap"><AccessCell user={u} /></td>
                  <td>{u.is_active ? <Badge tone="good">Ativo</Badge> : <Badge tone="neutral">Inativo</Badge>}</td>
                  <td>{fmtDateTime(u.last_login_at)}</td>
                  <td className="row-actions">
                    <button className="btn btn-sm" onClick={() => setEditing(u)}>Acessos</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </Card>
      {editing && <UserAccessModal user={editing} onClose={() => setEditing(null)} onChanged={reload} />}
    </>
  );
}
