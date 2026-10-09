import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { api, type CostCenter } from "../api";

/** Item da busca: página do menu, centro de custo ou ação (tema, senha, sair, recolher o menu). */
export interface PaletteItem {
  key: string;
  group: string;
  label: string;
  hint?: string;
  icon?: string;
  run: () => void;
  keywords?: string;
}

const norm = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

// "capex dados" ou "pessoal 011": a palavra do tipo escolhe a tela do CC aberta no Enter
const MODULES = [
  { key: "opex", label: "OPEX", path: "orcamento", words: ["opex", "orcamento"] },
  { key: "capex", label: "CAPEX", path: "capex", words: ["capex"] },
  { key: "pessoal", label: "Pessoal", path: "pessoal", words: ["pessoal", "pessoas"] },
];

let ccCache: CostCenter[] | null = null; // os CCs visíveis ao usuário, carregados na primeira abertura

/**
 * Busca rápida (Ctrl+K / ⌘K): páginas do menu, centros de custo (abre OPEX, CAPEX ou Pessoal do CC) e ações.
 * Setas escolhem, Enter abre, Esc fecha. Os CCs vêm de /cost-centers (já filtrados pelo escopo do usuário).
 */
export function CommandPalette({ items, onClose }: { items: PaletteItem[]; onClose: () => void }) {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [ccs, setCcs] = useState<CostCenter[] | null>(ccCache);
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);

  useEffect(() => {
    input.current?.focus();
    if (ccCache) return;
    api<CostCenter[]>("/cost-centers")
      .then((rows) => {
        ccCache = rows.filter((c) => c.is_active);
        setCcs(ccCache);
      })
      .catch(() => setCcs([]));
  }, []);

  const results = useMemo(() => {
    const words = norm(q).split(/\s+/).filter(Boolean);
    const mod = MODULES.find((m) => words.some((w) => m.words.includes(w)));
    const terms = words.filter((w) => !MODULES.some((m) => m.words.includes(w)));
    const match = (text: string) => terms.every((t) => norm(text).includes(t));
    const out: PaletteItem[] = items.filter((i) => words.every((w) => norm(`${i.label} ${i.keywords ?? ""}`).includes(w)));
    if (terms.length && ccs) {
      const target = mod ?? MODULES[0];
      ccs
        .filter((c) => match(`${c.code} ${c.name} ${c.manager_name ?? ""}`))
        .slice(0, 8)
        .forEach((c) =>
          out.push({
            key: `cc-${c.id}`,
            group: "Centros de custo",
            label: `${c.code} · ${c.name}`,
            hint: c.manager_name ?? undefined,
            run: () => navigate(`/${target.path}/${c.id}`),
            keywords: String(c.id),
          }),
        );
    }
    return out;
  }, [q, ccs, items, navigate]);

  useEffect(() => setActive(0), [q]);
  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  function choose(item?: PaletteItem) {
    if (!item) return;
    onClose();
    item.run();
  }
  function openCc(item: PaletteItem, path: string) {
    onClose();
    navigate(`/${path}/${item.keywords}`);
  }
  function onKey(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(results[active]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  }

  let lastGroup = "";
  return createPortal(
    <div className="palette-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Buscar">
        <div className="palette-search">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden>
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-3.5-3.5" />
          </svg>
          <input
            ref={input}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKey}
            placeholder="Buscar páginas, centros de custo e ações…"
            aria-label="Buscar"
            aria-controls="palette-results"
            aria-activedescendant={results[active] ? `palette-${results[active].key}` : undefined}
          />
          <kbd>Esc</kbd>
        </div>
        <div className="palette-results" id="palette-results" role="listbox" ref={list}>
          {results.length === 0 && (
            <p className="palette-empty">{ccs === null && q ? "Carregando centros de custo…" : "Nada encontrado."}</p>
          )}
          {results.map((item, i) => {
            const head = item.group !== lastGroup ? item.group : null;
            lastGroup = item.group;
            const isCc = item.key.startsWith("cc-");
            return (
              <div key={item.key}>
                {head && <div className="palette-group">{head}</div>}
                <div
                  id={`palette-${item.key}`}
                  role="option"
                  aria-selected={i === active}
                  data-index={i}
                  className={`palette-item${i === active ? " is-active" : ""}`}
                  onMouseMove={() => setActive(i)}
                  onClick={() => choose(item)}
                >
                  {item.icon && (
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                      <path d={item.icon} />
                    </svg>
                  )}
                  <span className="palette-label">{item.label}</span>
                  {item.hint && <span className="palette-hint">{item.hint}</span>}
                  {isCc && (
                    <span className="palette-chips">
                      {MODULES.map((m) => (
                        <button
                          key={m.key}
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            openCc(item, m.path);
                          }}
                        >
                          {m.label}
                        </button>
                      ))}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
        <div className="palette-foot">
          <span><kbd>↑</kbd><kbd>↓</kbd> escolher</span>
          <span><kbd>Enter</kbd> abrir</span>
          <span>Dica: "capex 011" abre o CAPEX do CC</span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
