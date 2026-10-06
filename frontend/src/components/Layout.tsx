import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { api } from "../api";
import { BrandMark } from "./Brand";
import { Alert, Modal } from "./ui";
import { useAuth } from "../auth";
import { ROLE_LABELS } from "../labels";
import { ThemeSwitch, useTheme, type Theme } from "../theme";

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
  const [more, setMore] = useState(false); // folha "Mais" da barra inferior (celular)
  const [theme, setTheme] = useTheme();
  const location = useLocation();
  useEffect(() => setMore(false), [location.pathname]);
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <BrandMark />
          <div>
            <strong>ATEM</strong>
            <span>Orçamento 2027</span>
          </div>
        </div>
        <nav id="main-nav">
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
          <div className="theme-row"><span className="lab">Tema</span><ThemeSwitch theme={theme} onChange={setTheme} /></div>
          {pwd && <PasswordModal onClose={() => setPwd(false)} />}
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
      <MobileTabs can={can} more={more} setMore={setMore} onPassword={() => setPwd(true)} onLogout={logout} userName={user?.name} theme={theme} setTheme={setTheme} />
    </div>
  );
}

// Barra de abas inferior (celular): destinos principais + "Mais" com o restante da navegação e a conta.
const TABS = [
  { to: "/", label: "Painel", end: true, icon: "M3 11 12 4l9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" },
  { to: "/orcamento", label: "OPEX", icon: "M4 19h16M6 16V9m4 7V5m4 11v-6m4 6V7" },
  { to: "/capex", label: "CAPEX", icon: "M3 20h18M5 20V9l7-5 7 5v11M10 20v-6h4v6" },
  { to: "/pessoal", label: "Pessoal", icon: "M16 11a4 4 0 1 0-8 0 4 4 0 0 0 8 0zM4 21a8 8 0 0 1 16 0" },
];

function Icon({ d }: { d: string }) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={d} />
    </svg>
  );
}

function MobileTabs({ can, more, setMore, onPassword, onLogout, userName, theme, setTheme }: {
  can: (...roles: string[]) => boolean; more: boolean; setMore: (v: boolean) => void;
  onPassword: () => void; onLogout: () => void; userName?: string; theme: Theme; setTheme: (t: Theme) => void;
}) {
  const rest = NAV.filter((n) => !TABS.some((t) => t.to === n.to) && (!n.roles || can(...n.roles)));
  return (
    <>
      {more && (
        <div className="sheet-backdrop" onClick={() => setMore(false)}>
          <div className="sheet" role="dialog" aria-label="Mais opções" onClick={(e) => e.stopPropagation()}>
            <div className="sheet-handle" aria-hidden />
            <nav className="sheet-nav">
              {rest.map((n) => (
                <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? "active" : "")}>
                  {n.label}
                </NavLink>
              ))}
            </nav>
            <div className="sheet-foot">
              <span className="user-name">{userName}</span>
              <button className="btn btn-ghost btn-sm" onClick={() => { setMore(false); onPassword(); }}>Alterar senha</button>
              <button className="btn btn-ghost btn-sm" onClick={onLogout}>Sair</button>
            </div>
            <div className="theme-row"><span className="lab">Tema</span><ThemeSwitch theme={theme} onChange={setTheme} /></div>
          </div>
        </div>
      )}
      <nav className="tabbar" aria-label="Navegação principal">
        {TABS.map((t) => (
          <NavLink key={t.to} to={t.to} end={t.end} className={({ isActive }) => (isActive && !more ? "active" : "")}>
            <Icon d={t.icon} />
            <span>{t.label}</span>
          </NavLink>
        ))}
        <button type="button" className={more ? "active" : ""} aria-expanded={more} onClick={() => setMore(!more)}>
          <Icon d="M5 12h.01M12 12h.01M19 12h.01" />
          <span>Mais</span>
        </button>
      </nav>
    </>
  );
}
