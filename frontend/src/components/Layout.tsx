import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth";
import { ROLE_LABELS } from "../labels";

const NAV = [
  { to: "/", label: "Painel", end: true },
  { to: "/orcamento", label: "Orçamento OPEX" },
  { to: "/capex", label: "Orçamento CAPEX" },
  { to: "/pessoal", label: "Orçamento de Pessoal" },
  { to: "/importacoes", label: "Importação de dados", roles: ["CONTROLLER"] },
  { to: "/cadastros", label: "Cadastros" },
  { to: "/ciclo", label: "Ciclo e parâmetros" },
  { to: "/usuarios", label: "Usuários", roles: ["CONTROLLER"] },
  { to: "/auditoria", label: "Auditoria", roles: ["CONTROLLER"] },
];

const SOON = ["Consolidação e exportação"];

export default function Layout() {
  const { user, logout, can } = useAuth();
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
          <div className="nav-section">Próximas fases</div>
          {SOON.map((s) => (
            <span key={s} className="nav-disabled">
              {s}
            </span>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="user-name">{user?.name}</div>
          <div className="muted small">{user?.roles.map((r) => ROLE_LABELS[r] ?? r).join(", ")}</div>
          <button className="btn btn-ghost btn-sm" onClick={logout}>
            Sair
          </button>
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
