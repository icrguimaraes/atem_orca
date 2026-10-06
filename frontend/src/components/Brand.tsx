import { useState } from "react";

/**
 * Marca do grupo. Usa a logo oficial em `public/brand/logo.svg` (ou .png) quando o arquivo existir;
 * sem o arquivo, cai no monograma "A" do design system.
 */
export function BrandMark({ size = 40 }: { size?: number }) {
  const [src, setSrc] = useState<string | null>("/brand/logo.svg");
  if (!src) return <span className="brand-mark" style={{ width: size, height: size }}>A</span>;
  return (
    <img
      className="brand-logo"
      src={src}
      alt="Grupo Atem"
      style={{ height: size }}
      onError={() => setSrc(src.endsWith(".svg") ? "/brand/logo.png" : null)}
    />
  );
}
