/**
 * Marca do Grupo Atem (fundo transparente). O CSS mostra a variante conforme o tema (automático ou escolhido em data-theme).
 * `compact` (barra lateral e barra do celular): as versões compactas da Prévia de Resultados (`logo-atem*.png`);
 * sem ele (login): a logo grande (`logo.png` / `logo-dark.png`).
 */
export function BrandMark({ size = 44, compact = false }: { size?: number; compact?: boolean }) {
  const light = compact ? "/brand/logo-atem.png" : "/brand/logo.png";
  const dark = compact ? "/brand/logo-atem-claro.png" : "/brand/logo-dark.png";
  return (
    <span className="brand-logo" style={{ height: size }}>
      <img className="logo-light" src={light} alt="Grupo Atem" style={{ height: size }} />
      <img className="logo-dark" src={dark} alt="Grupo Atem" style={{ height: size }} />
    </span>
  );
}
