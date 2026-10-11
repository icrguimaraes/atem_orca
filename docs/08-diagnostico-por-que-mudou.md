# Diagnóstico — painel "Por que mudou?" (10/10/2026)

Componente: `frontend/src/components/WhyPanel.tsx` (`WhyButton` = resumo no hover + `WhyDrawer` = painel lateral),
usado na tabela do Painel (`DrillTable`), nos destaques de desvio (`DeviationHighlights`) e em `/perguntas`
(`pages/Questions.tsx`, botão "Ver o por quê?"). Dados: `GET /dashboard/why` (recorte: totais, por tipo, decomposição
por conta/CC, itens a justificar OPEX/Pessoal/CAPEX, alertas, perguntas) e `GET /dashboard/why/lines` (lançamentos OPEX,
paginado, busca em descrição/fornecedor/justificativa/conta/CC). Ações: `POST /questions`, `/questions/{id}/answer|close`.

## O que pesava

1. **Tudo aberto em sequência**: total, cards por tipo, alertas, barras, lista "o que o gestor disse", 20 lançamentos com
   busca, pessoal, CAPEX e perguntas — sem hierarquia; a decisão ("está justificado? há pergunta pendente?") ficava no fim.
2. **Números de tipos diferentes**: total por extenso e cards por tipo abreviados (`fmtCompact`, "R$ 1,2 mi").
3. **Cor enganosa**: todo aumento em vermelho e toda queda em verde (`tone`), ignorando o semáforo do ciclo
   (`alert.growth_pct`/`alert.reduction_pct`) usado na tabela do Painel; barras de largura cheia em vermelho/verde.
4. **Laranja** no alerta de reclassificação e nos selos "pergunta aberta" (`badge-warn`), fora da paleta pedida.
5. **Status espalhado**: "sem justificativa" só como alerta no meio do corpo; perguntas pendentes só no fim.
6. **Rodapé com três botões iguais**, sem ação principal; "Ver rastro" não existia no painel.
7. Perguntas sempre todas abertas, da mais nova para a mais antiga, com o formulário de resposta embutido.

## O que a API já oferece (e o que não)

- Justificativa da conta, da movimentação e do CAPEX: só o texto e `justified` — **sem autor nem data** no
  `/dashboard/why` (o modelo `AccountJustification` tem `updated_by/updated_at`, mas não são expostos; linhas,
  movimentações e CAPEX não têm esse registro). O painel não mostra autor/data da justificativa.
- Perguntas: autor e data da pergunta e da resposta (`asked_by/asked_at`, `answered_by/answered_at`).
- Limiares do semáforo: não vinham no `/dashboard/why` — passaram a vir em `thresholds` (mesma função `_thresholds` do
  `/dashboard/breakdown`), única mudança no backend.

## Nova organização (sem remover função)

Painel com 800 px (antes 640 px; tela cheia até 600 px), cabeçalho e rodapé fixos e uma só área de rolagem; seções
separadas por espaço e filetes, sem caixas cinza nem um card por item.

- **Cabeçalho**: "Por que mudou?" discreto, nome do item, setor/CC na linha de baixo, períodos comparados ("comparação
  entre anos") e a situação em texto com ponto (n sem justificativa, n perguntas pendentes — clicáveis), fechar.
- **Resumo**: base, orçamento e variação em três colunas iguais, mesmo tamanho e alinhamento (a variação leva cor do
  semáforo do ciclo, ▲/▼, % e a frase da faixa); composição por tipo em colunas alinhadas (só com mais de um tipo).
- **Avisos**: linhas curtas com ícone e ação ("Ver itens", "Questionar"); vermelho só no crítico.
- **Justificativas do gestor (centro)**: abas por tipo, "Só sem justificativa", filtro por CC; cada linha com código e
  nome, contexto, um indicador (sem justificativa / pergunta pendente / justificado) e base / orçamento / variação nas
  mesmas colunas do resumo; prévia do texto; abrir a linha mostra o texto inteiro, os lançamentos da conta (com Meses e
  Questionar) ou "Questionar este item" (pessoal/CAPEX) e as perguntas do item.
- **Origem da variação**: síntese (5 maiores, por conta ou por CC, barra fina); clicar abre a conta na lista ou filtra
  pelo CC.
- **Investigar**: lançamentos OPEX recolhidos ("n lançamentos · R$ total", busca) e o rastro do recorte.
- **Perguntas**: ordem cronológica, autor/data/situação, pendentes sempre visíveis, "ver histórico"; resposta sob demanda.
- **Rodapé**: "Questionar" em destaque só quando há item sem justificativa ou pergunta pendente; Ver rastro, Orçamento
  do CC, Justificativas e Todas as perguntas como links (no celular, no menu "Mais").
