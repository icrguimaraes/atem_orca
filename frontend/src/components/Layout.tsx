import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { api } from "../api";
import { Alert, Modal } from "./ui";
import { useAuth } from "../auth";
import { ROLE_LABELS } from "../labels";

const NAV = [
  { to: "/", label: "Painel", end: true },
  { to: "/analise", label: "Análise orçamentária" },
  { to: "/orcamento", label: "Orçamento OPEX" },
  { to: "/capex", label: "Orçamento CAPEX" },
  { to: "/pessoal", label: "Orçamento de Pessoal" },
  { to: "/consolidacao", label: "Consolidação e exportação" },
  { to: "/importacoes", label: "Importação de dados", roles: ["CONTROLLER"] },
  { to: "/cadastros", label: "Cadastros" },
  { to: "/ciclo", label: "Ciclo e parâmetros" },
  { to: "/usuarios", label: "Usuários", roles: ["CONTROLLER"] },
  { to: "/auditoria", label: "Auditoria", roles: ["CONTROLLER"] },
];

const SOON: string[] = [];

function PasswordModal({ onClose }: { onClose: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  async function save() {
    if (next !== confirm) return setError("A confirmação não confere com a nova senha");
    setError(null);
    try {
      await api("/auth/change-password", { method: "POST", body: JSON.stringify({ current_password: current, new_password: next }) });
      setDone(true);
    } catch (err) {
      setError((err as Error).message);
    }
  }
  return (
    <Modal
      title="Alterar minha senha"
      onClose={onClose}
      footer={done ? <button className="btn btn-primary" onClick={onClose}>Fechar</button> : (
        <>
          <button className="btn btn-ghost" onClick={onClose}>Cancelar</button>
          <button className="btn btn-primary" disabled={!current || next.length < 8} onClick={save}>Salvar</button>
        </>
      )}
    >
      {done ? <Alert tone="good">Senha alterada. Use a nova senha no próximo acesso.</Alert> : (
        <div className="stack">
          {error && <Alert>{error}</Alert>}
          <label>Senha atual<input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} autoFocus /></label>
          <label>Nova senha (mínimo 8 caracteres)<input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} /></label>
          <label>Confirmar nova senha<input type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} /></label>
        </div>
      )}
    </Modal>
  );
}

export default function Layout() {
  const { user, logout, can } = useAuth();
  const [pwd, setPwd] = useState(false);
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">A</span>
          <div>
            <strong>ATEM</strong>
            <span>Orçamento 2027</span>
          </div>
        </div>
        <nav>
          {NAV.filter((n) => !n.roles || can(...n.roles)).map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? "active" : "")}>
              {n.label}
            </NavLink>
          ))}
          {SOON.length > 0 && <div className="nav-section">Próximas fases</div>}
          {SOON.map((s) => (
            <span key={s} className="nav-disabled">
              {s}
            </span>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="user-name">{user?.name}</div>
          <div className="muted small">{user?.roles.map((r) => ROLE_LABELS[r] ?? r).join(", ")}</div>
          <div className="inline-controls">
            <button className="btn btn-ghost btn-sm" onClick={() => setPwd(true)}>Alterar senha</button>
            <button className="btn btn-ghost btn-sm" onClick={logout}>Sair</button>
          </div>
          {pwd && <PasswordModal onClose={() => setPwd(false)} />}
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
