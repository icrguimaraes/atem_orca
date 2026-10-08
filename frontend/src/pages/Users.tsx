import { useState } from "react";
import { api, type UserListItem } from "../api";
import { useAuth } from "../auth";
import { Alert, Badge, Card, Loading, PageHeader, useLoad } from "../components/ui";
import { AccessCell, UserAccessModal } from "../components/UserAccess";
import { UserEditor } from "../components/UserEditor";
import { ROLE_LABELS, fmtDateTime } from "../labels";

export default function Users() {
  const { can } = useAuth();
  const { data, error, reload } = useLoad(() => api<UserListItem[]>("/users"));
  const [editing, setEditing] = useState<UserListItem | null>(null);
  const [form, setForm] = useState<{ user: UserListItem | null } | null>(null);  // user null = novo
  const [ok, setOk] = useState<string | null>(null);
  return (
    <>
      <PageHeader
        title="Usuários"
        subtitle="Cada usuário enxerga os centros de custo em que é gestor (cadastro do CC) e os marcados no cadastro dele; Administrador e Controladoria veem todos."
        actions={can() && <button className="btn btn-primary" onClick={() => { setOk(null); setForm({ user: null }); }}>Novo usuário</button>}
      />
      {ok && <Alert tone="good">{ok}</Alert>}
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
                    {can() && <button className="btn btn-sm" onClick={() => { setOk(null); setForm({ user: u }); }}>Editar</button>}
                    <button className="btn btn-sm btn-ghost" onClick={() => setEditing(u)} title="Ver acessos e liberar uma empresa inteira">Acessos</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </Card>
      {editing && <UserAccessModal user={editing} onClose={() => setEditing(null)} onChanged={reload} />}
      {form && <UserEditor user={form.user} onClose={() => setForm(null)} onSaved={(msg) => { setOk(msg); reload(); }} />}
    </>
  );
}
