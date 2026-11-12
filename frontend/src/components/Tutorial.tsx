/**
 * Botão "Como funciona" e o tutorial da tela, num painel lateral (12/11/2026). O conteúdo vem de `ajuda.ts` pela rota
 * atual (ou por um caminho explícito, para modais e abas). Só abre quando o usuário clica; fecha com Esc, × ou clique
 * fora.
 */
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useLocation } from "react-router-dom";
import { FLUXO, ajudaDaRota, type Ajuda } from "../ajuda";

export function useAjuda(caminho?: string) {
  const { pathname } = useLocation();
  return ajudaDaRota(caminho ?? pathname);
}

export function BotaoTutorial({ caminho }: { caminho?: string }) {
  const ajuda = useAjuda(caminho);
  const [aberto, setAberto] = useState(false);
  if (!ajuda) return null;
  return (
    <>
      <button
        type="button"
        className="btn btn-ghost btn-sm btn-tutorial"
        onClick={() => setAberto(true)}
        aria-haspopup="dialog"
        title="O que é esta tela e como usar"
      >
        <span className="ic" aria-hidden="true">?</span>
        Como funciona
      </button>
      {aberto && <PainelTutorial ajuda={ajuda} fechar={() => setAberto(false)} />}
    </>
  );
}

function PainelTutorial({ ajuda, fechar }: { ajuda: Ajuda & { etapa?: number }; fechar: () => void }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopImmediatePropagation();
      fechar();
    };
    window.addEventListener("keydown", esc, true);
    return () => window.removeEventListener("keydown", esc, true);
  }, [fechar]);
  return createPortal(
    <div className="tut-fundo" onClick={fechar}>
      <aside className="tut" role="dialog" aria-modal="true" aria-label={`Como funciona: ${ajuda.titulo}`} onClick={(e) => e.stopPropagation()}>
        <header>
          <div>
            <div className="k">Como funciona</div>
            <h2>{ajuda.titulo}</h2>
            <p className="resumo">{ajuda.resumo}</p>
          </div>
          <button type="button" className="x" onClick={fechar} aria-label="Fechar" autoFocus>×</button>
        </header>
        <div className="corpo">
          <Secao titulo="O que faz" itens={ajuda.oQueFaz} />
          <Secao titulo="Onde afeta" itens={ajuda.ondeAfeta} />
          <section>
            <h3>Como usar</h3>
            <ol className="passos">
              {ajuda.comoUsar.map((p) => <li key={p}>{p}</li>)}
            </ol>
          </section>
          {ajuda.bomSaber && <Secao titulo="Bom saber" itens={ajuda.bomSaber} nota />}
        </div>
        <footer>
          <div className="k">Onde esta tela está no fluxo</div>
          <ol className="fluxo">
            {FLUXO.map((f, i) => (
              <li key={f} className={ajuda.etapa === i ? "on" : ""} aria-current={ajuda.etapa === i ? "step" : undefined}>{f}</li>
            ))}
          </ol>
        </footer>
      </aside>
    </div>,
    document.body,
  );
}

function Secao({ titulo, itens, nota }: { titulo: string; itens: string[]; nota?: boolean }) {
  return (
    <section className={nota ? "nota" : undefined}>
      <h3>{titulo}</h3>
      <ul>{itens.map((t) => <li key={t}>{t}</li>)}</ul>
    </section>
  );
}
