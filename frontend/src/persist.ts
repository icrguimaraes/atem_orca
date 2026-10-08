import { useEffect, useState, type Dispatch, type SetStateAction } from "react";

/* Filtros das páginas guardados no navegador até o próximo login/logout (pedido de 08/10/2026): ao voltar a uma
   página, os filtros continuam como estavam. Tudo com try/catch — sem armazenamento, vale o estado inicial. */

const PREFIX = "atem.filters.";

/** useState persistido em localStorage. `force` ignora o valor salvo (ex.: filtro que veio pela URL). */
export function usePersistentState<T>(key: string, initial: T | (() => T), force = false): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => {
    if (!force) {
      try {
        const raw = localStorage.getItem(PREFIX + key);
        if (raw !== null) return JSON.parse(raw) as T;
      } catch {
        /* sem armazenamento: estado inicial */
      }
    }
    return typeof initial === "function" ? (initial as () => T)() : initial;
  });
  useEffect(() => {
    try {
      localStorage.setItem(PREFIX + key, JSON.stringify(value));
    } catch {
      /* ignora */
    }
  }, [key, value]);
  return [value, setValue];
}

/** Apaga os filtros guardados (no login e no logout). */
export function clearPersistedFilters(): void {
  try {
    Object.keys(localStorage).filter((k) => k.startsWith(PREFIX)).forEach((k) => localStorage.removeItem(k));
  } catch {
    /* ignora */
  }
}
