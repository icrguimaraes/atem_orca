/**
 * Marca do Grupo Atem (`public/brand/logo.png` e `logo-dark.png`, fundo transparente).
 * O CSS mostra a variante conforme o tema (automático ou escolhido em data-theme).
 */
export function BrandMark({ size = 44 }: { size?: number }) {
  return (
    <span className="brand-logo" style={{ height: size }}>
      <img className="logo-light" src="/brand/logo.png" alt="Grupo Atem" style={{ height: size }} />
      <img className="logo-dark" src="/brand/logo-dark.png" alt="Grupo Atem" style={{ height: size }} />
    </span>
  );
}
