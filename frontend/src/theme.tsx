import { useCallback, useEffect, useState } from "react";

export type Theme = "auto" | "light" | "dark";
const KEY = "atem.theme";

/** Aplica o tema no <html> (data-theme); "auto" segue o sistema. O mesmo código roda inline em index.html
 *  antes do React, para a página não piscar no tema errado. */
export function applyTheme(theme: Theme) {
  const root = document.documentElement;
  if (theme === "auto") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
  root.style.colorScheme = theme === "auto" ? "" : theme;
}

export function readTheme(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "auto";
  } catch {
    return "auto";
  }
}

export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setThemeState] = useState<Theme>(readTheme);
  useEffect(() => applyTheme(theme), [theme]);
  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
    try {
      if (t === "auto") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, t);
    } catch {
      /* armazenamento indisponível: vale só para a sessão */
    }
  }, []);
  return [theme, setTheme];
}

export const THEME_OPTIONS: { value: Theme; label: string; title: string }[] = [
  { value: "auto", label: "Auto", title: "Seguir o sistema" },
  { value: "light", label: "Claro", title: "Tema claro" },
  { value: "dark", label: "Escuro", title: "Tema escuro" },
];

export function ThemeSwitch({ theme, onChange }: { theme: Theme; onChange: (t: Theme) => void }) {
  return (
    <div className="theme-switch" role="radiogroup" aria-label="Tema">
      {THEME_OPTIONS.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={theme === o.value} title={o.title} className={theme === o.value ? "active" : ""} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
