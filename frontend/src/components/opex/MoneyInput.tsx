import { useEffect, useState, type ClipboardEvent } from "react";

/** Converte "1.234,56", "1234.56", "R$ 1.234" → número. Vazio = 0. */
export function parseMoney(text: string): number | null {
  const clean = text.replace(/R\$|\s/g, "");
  if (!clean) return 0;
  // pt-BR: "1.234,56" e "2.000" (ponto como milhar); "1234.56" só quando não segue o padrão de milhar
  const normalized = clean.includes(",")
    ? clean.replace(/\./g, "").replace(",", ".")
    : /^\d{1,3}(\.\d{3})+$/.test(clean)
      ? clean.replace(/\./g, "")
      : clean;
  const n = Number(normalized);
  return Number.isFinite(n) ? n : null;
}

const fmt = (n: number) => (n ? n.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "");

/** Campo de valor com formatação pt-BR; colar várias células do Excel distribui pelos meses seguintes. */
export function MoneyInput({
  value,
  disabled,
  onCommit,
  onPasteMany,
  label,
}: {
  value: string | number;
  disabled?: boolean;
  onCommit: (n: number) => void;
  onPasteMany?: (values: number[]) => void;
  label?: string;
}) {
  const [text, setText] = useState(fmt(Number(value)));
  const [invalid, setInvalid] = useState(false);
  useEffect(() => setText(fmt(Number(value))), [value]);

  function commit() {
    const n = parseMoney(text);
    if (n === null || n < 0) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    setText(fmt(n));
    if (Math.abs(n - Number(value)) > 0.004) onCommit(Math.round(n * 100) / 100);
  }

  function paste(e: ClipboardEvent<HTMLInputElement>) {
    const raw = e.clipboardData.getData("text");
    const parts = raw.trim().split(/[\t\n\r]+/).filter(Boolean);
    if (parts.length > 1 && onPasteMany) {
      e.preventDefault();
      const nums = parts.map((p) => parseMoney(p) ?? 0);
      onPasteMany(nums);
    }
  }

  return (
    <input
      className={`money ${invalid ? "invalid" : ""}`}
      inputMode="decimal"
      aria-label={label}
      value={text}
      disabled={disabled}
      placeholder="0"
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
      onPaste={paste}
    />
  );
}
