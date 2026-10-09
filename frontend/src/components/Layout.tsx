import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api";
import { BrandMark } from "./Brand";
import { CommandPalette, type PaletteItem } from "./CommandPalette";
import { Alert, Modal } from "./ui";
import { useAuth } from "../auth";
import { ROLE_LABELS } from "../labels";
import { ThemeSwitch, useTheme, type Theme } from "../theme";

// `pj`: só para quem tem "Vê contratos PJ" (flag do usuário, não perfil)
interface NavItem { to: string; label: string; icon: string; end?: boolean; roles?: string[]; pj?: boolean }

// traço de 24 px (stroke), o mesmo desenho no menu lateral, na barra de abas e na folha "Mais"
// (mesmo padrão do menu do projeto Movimentação de Pessoal)
const ICON = {
  painel: "M4 4h7v7H4zM13 4h7v4h-7zM13 10h7v10h-7zM4 13h7v7H4z",
  painel2: "M4 19h16M5 15l4-4 3 3 7-7M15 7h4v4",
  analise: "M4 19h16M6 16V9m4 7V5m4 11v-6m4 6V7",
  consolidacao: "M12 3 3 7.5l9 4.5 9-4.5L12 3zM3 12l9 4.5 9-4.5M3 16.5l9 4.5 9-4.5",
  apontamentos: "M12 4 2.5 20h19L12 4zM12 10v4M12 17h.01",
  justificativas: "M5 4h14v12H9l-4 4zM9 9h6M9 12h4",
  perguntas: "M4 5h16v11H8l-4 4zM10 9a2 2 0 1 1 3 1.7c-.6.4-1 .8-1 1.3M12 14h.01",
  validacao: "M4 4h16v16H4zM4 9h16M4 14h16M9 4v16M14 4v16",
  importacoes: "M12 15V4m0 0-4 4m4-4 4 4M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4",
  cadastros: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6",
  estrutura: "M9 3h6v5H9zM12 8v3M6 14v-3h12v3M3 14h6v5H3zM15 14h6v5h-6z",
  ciclo: "M4 6h9m4 0h3M4 12h3m4 0h9M4 18h11m4 0h1M15 4v4M9 10v4M17 16v4",
  usuarios: "M16 20v-1.5a3.5 3.5 0 0 0-3.5-3.5h-5A3.5 3.5 0 0 0 4 18.5V20M10 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM20 20v-1.5a3.5 3.5 0 0 0-2.5-3.35M15.5 4.15a3.5 3.5 0 0 1 0 6.7",
  base: "M4 19h16M7 16v-5M12 16V7M17 16v-8M4 5h4",
  auditoria: "M12 3 5 6v5c0 4.5 3 8.3 7 9.5 4-1.2 7-5 7-9.5V6l-7-3zM9 12l2 2 4-4",
  pj: "M4 7h16v12H4zM9 7V4h6v3M4 12h16M12 11v3",
  senha: "M21 2l-2 2m-7.6 7.6a5.5 5.5 0 1 1-7.8 7.8 5.5 5.5 0 0 1 7.8-7.8zm0 0L15.5 7.5m0 0 3 3L22 7l-3-3m-3.5 3.5L19 4",
  sair: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9",
  mais: "M5 12h.01M12 12h.01M19 12h.01",
  recolher: "M4 4h16v16H4zM9 4v16M15 10l-2 2 2 2",
  expandir: "M4 4h16v16H4zM9 4v16M13 10l2 2-2 2",
  busca: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-3.5-3.5",
  tema: "M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9z",
};

const RAIL_KEY = "atem.sidebar.collapsed"; // menu recolhido: preferência do navegador (não some no login)
const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);

const NAV_GROUPS: { title: string; items: NavItem[] }[] = [
  {
    title: "Orçamento",
    items: [
      { to: "/", label: "Painel", end: true, icon: ICON.painel },
      { to: "/analise", label: "Análise orçamentária", icon: ICON.analise },
      { to: "/consolidacao", label: "Consolidação e exportação", icon: ICON.consolidacao },
      { to: "/apontamentos", label: "Apontamentos", icon: ICON.apontamentos },
      { to: "/justificativas", label: "Justificativas", icon: ICON.justificativas },
      { to: "/perguntas", label: "Perguntas", icon: ICON.perguntas },
      { to: "/validacao", label: "Validação", roles: ["CONTROLLER"], icon: ICON.validacao },
      { to: "/pj", label: "Contratos PJ", pj: true, icon: ICON.pj },
    ],
  },
  {
    title: "Administração",
    items: [
      { to: "/importacoes", label: "Importação de dados", roles: ["CONTROLLER"], icon: ICON.importacoes },
      { to: "/base", label: "Situação da base", roles: ["CONTROLLER"], icon: ICON.base },
      { to: "/cadastros", label: "Cadastros", roles: ["CONTROLLER"], icon: ICON.cadastros },
      { to: "/estrutura", label: "Áreas e setores", roles: ["CONTROLLER"], icon: ICON.estrutura },
      { to: "/ciclo", label: "Ciclo e parâmetros", roles: ["CONTROLLER"], icon: ICON.ciclo },
      { to: "/usuarios", label: "Usuários", roles: ["CONTROLLER"], icon: ICON.usuarios },
      { to: "/auditoria", label: "Auditoria", roles: ["CONTROLLER"], icon: ICON.auditoria },
    ],
  },
];
const NAV = NAV_GROUPS.flatMap((g) => g.items);

/** Iniciais do nome (primeiro e último), para o avatar do rodapé do menu. */
function initials(name?: string) {
  const parts = (name ?? "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

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
  // item visível: perfil (`roles`) e, para Contratos PJ, a flag "Vê contratos PJ" do usuário
  const allowed = (n: NavItem) => (!n.roles || can(...n.roles)) && (!n.pj || (user?.pj_access ?? "NONE") !== "NONE");
  const [pwd, setPwd] = useState(false);
  const [more, setMore] = useState(false); // folha "Mais" da barra inferior (celular)
  const [theme, setTheme] = useTheme();
  const location = useLocation();
  const navigate = useNavigate();
  useEffect(() => setMore(false), [location.pathname]);
  // menu lateral retrátil (só ícones) para dar mais espaço às páginas
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(RAIL_KEY) === "1";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(RAIL_KEY, collapsed ? "1" : "0");
    } catch {
      /* ignora */
    }
  }, [collapsed]);
  // busca rápida: Ctrl+K (⌘K no Mac) em qualquer página
  const [search, setSearch] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearch((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const paletteItems: PaletteItem[] = [
    ...NAV_GROUPS.flatMap((g) =>
      g.items.filter(allowed).map((n) => ({ key: n.to, group: "Páginas", label: n.label, icon: n.icon, hint: g.title, run: () => navigate(n.to) })),
    ),
    { key: "rail", group: "Ações", label: collapsed ? "Expandir o menu lateral" : "Recolher o menu lateral", icon: collapsed ? ICON.expandir : ICON.recolher, run: () => setCollapsed(!collapsed), keywords: "menu barra lateral" },
    { key: "theme", group: "Ações", label: theme === "dark" ? "Usar tema claro" : "Usar tema escuro", icon: ICON.tema, run: () => setTheme(theme === "dark" ? "light" : "dark"), keywords: "tema escuro claro modo noturno" },
    { key: "pwd", group: "Ações", label: "Alterar minha senha", icon: ICON.senha, run: () => setPwd(true), keywords: "senha conta" },
    { key: "logout", group: "Ações", label: "Sair", icon: ICON.sair, run: logout, keywords: "logout sair" },
  ];
  return (
    <div className={`shell${collapsed ? " is-collapsed" : ""}`}>
      <aside className="sidebar">
        <div className="brand">
          <BrandMark size={collapsed ? 30 : 44} />
          <div className="brand-text">
            <strong>Orçamento</strong>
            <span>Controladoria e Tributos · Grupo Atem</span>
          </div>
          <button type="button" className="mobile-only top-search-icon" onClick={() => setSearch(true)} aria-label="Buscar">
            <Icon d={ICON.busca} size={20} />
          </button>
        </div>
        <button
          type="button"
          className="rail-toggle"
          onClick={() => setCollapsed(!collapsed)}
          aria-expanded={!collapsed}
          aria-controls="main-nav"
          title={collapsed ? "Expandir o menu" : "Recolher o menu"}
        >
          <Icon d={collapsed ? ICON.expandir : ICON.recolher} size={18} />
          <span>Recolher menu</span>
        </button>
        <nav id="main-nav" aria-label="Navegação principal">
          {NAV_GROUPS.map((g) => {
            const items = g.items.filter(allowed);
            if (!items.length) return null;
            return (
              <div className="nav-group" key={g.title}>
                <div className="nav-section">{g.title}</div>
                {items.map((n) => (
                  <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? "active" : "")} title={collapsed ? n.label : undefined}>
                    <Icon d={n.icon} size={18} />
                    <span>{n.label}</span>
                  </NavLink>
                ))}
              </div>
            );
          })}
        </nav>
        <div className="sidebar-foot">
          <UserMenu
            name={user?.name}
            roles={user?.roles.map((r) => ROLE_LABELS[r] ?? r).join(", ")}
            theme={theme}
            setTheme={setTheme}
            onPassword={() => setPwd(true)}
            onLogout={logout}
          />
        </div>
      </aside>
      <div className="main-col">
        <header className="topbar">
          <button type="button" className="top-search" onClick={() => setSearch(true)}>
            <Icon d={ICON.busca} size={17} />
            <span>Buscar páginas, centros de custo e ações…</span>
            <kbd>{isMac ? "⌘" : "Ctrl"} K</kbd>
          </button>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
      {search && <CommandPalette items={paletteItems} onClose={() => setSearch(false)} />}
      {pwd && <PasswordModal onClose={() => setPwd(false)} />}
      <MobileTabs allowed={allowed} more={more} setMore={setMore} onPassword={() => setPwd(true)} onLogout={logout} userName={user?.name} theme={theme} setTheme={setTheme} />
    </div>
  );
}

/**
 * Conta no rodapé do menu: o cartão abre (para cima) senha e tema; o botão ao lado sai do sistema direto.
 */
function UserMenu({ name, roles, theme, setTheme, onPassword, onLogout }: {
  name?: string; roles?: string; theme: Theme; setTheme: (t: Theme) => void; onPassword: () => void; onLogout: () => void;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const location = useLocation();
  useEffect(() => setOpen(false), [location.pathname]);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        toggle.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  const run = (fn: () => void) => () => {
    setOpen(false);
    fn();
  };
  return (
    <div className="side-user-wrap" ref={wrap}>
      {open && (
        <div className="side-menu" id="side-user-menu">
          <button type="button" onClick={run(onPassword)}><Icon d={ICON.senha} size={16} />Alterar senha</button>
          <div className="side-menu-theme"><span>Tema</span><ThemeSwitch theme={theme} onChange={setTheme} /></div>
          <hr />
          <button type="button" onClick={run(onLogout)}><Icon d={ICON.sair} size={16} />Sair</button>
        </div>
      )}
      <div className="side-user">
        <button
          ref={toggle}
          type="button"
          className="side-user-btn"
          aria-expanded={open}
          aria-controls={open ? "side-user-menu" : undefined}
          onClick={() => setOpen(!open)}
          title="Minha conta: senha, tema e sair"
        >
          <span className="side-avatar" aria-hidden>{initials(name)}</span>
          <span className="side-user-text">
            <strong title={name}>{name}</strong>
            <span>{roles}</span>
          </span>
        </button>
        <button type="button" className="side-lock" onClick={onLogout} title="Sair" aria-label="Sair">
          <Icon d={ICON.sair} size={18} />
        </button>
      </div>
    </div>
  );
}

// Barra de abas inferior (celular): destinos principais + "Mais" com o restante da navegação e a conta.
const TABS = [
  { to: "/", label: "Painel", end: true, icon: ICON.painel },
  { to: "/analise", label: "Análise", icon: ICON.analise },
  { to: "/consolidacao", label: "Consolidação", icon: ICON.consolidacao },
];

function Icon({ d, size = 22 }: { d: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={d} />
    </svg>
  );
}

function MobileTabs({ allowed, more, setMore, onPassword, onLogout, userName, theme, setTheme }: {
  allowed: (n: NavItem) => boolean; more: boolean; setMore: (v: boolean) => void;
  onPassword: () => void; onLogout: () => void; userName?: string; theme: Theme; setTheme: (t: Theme) => void;
}) {
  const rest = NAV.filter((n) => !TABS.some((t) => t.to === n.to) && allowed(n));
  return (
    <>
      {more && (
        <div className="sheet-backdrop" onClick={() => setMore(false)}>
          <div className="sheet" role="dialog" aria-label="Mais opções" onClick={(e) => e.stopPropagation()}>
            <div className="sheet-handle" aria-hidden />
            <nav className="sheet-nav">
              {rest.map((n) => (
                <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? "active" : "")}>
                  <Icon d={n.icon} size={20} />
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
          <Icon d={ICON.mais} />
          <span>Mais</span>
        </button>
      </nav>
    </>
  );
}
