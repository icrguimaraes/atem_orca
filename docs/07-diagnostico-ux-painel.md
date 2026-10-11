# Diagnóstico de UX — Painel (etapas 1 e 2)

> 10/10/2026. Escopo: Painel (`frontend/src/pages/Painel2.tsx`, rota `/`). As demais telas ficam para a etapa 3.
> Backup antes da mudança: tag `backup-antes-redesign-2026-10-10`.

## Como o Painel funciona hoje

- **Pilha**: React 18 + Vite + TS; gráficos Plotly montados no backend (`services/painel_figures.py`) e desenhados por
  `components/PlotlyChart.tsx`. Números em `GET /dashboard/overview` (`api/v1/dashboard.py`: `Period` = anos somados ×
  meses × tipos; `Facts` = somas com escopo do usuário e filtros); tabela em `GET /dashboard/breakdown` (`DrillTable`).
- **Filtros → visuais**: estado em `usePersistentState` (empresa, área, CC, tipo, anos, meses, pacote, conta por clique,
  comparar, mesmo período) → uma `query` única → `overview?figures=true` (KPIs + figuras) e `breakdown` (tabela). Clique
  nos visuais altera o mesmo estado (o visual clicado destaca, os demais filtram).
- **Projeção do gestor** (out–dez 2026) entra como `ActualEntry.projected = true`; com ela carregada, `_last_closed`
  considera o ano completo.

## Problemas encontrados

### Hierarquia e leitura
1. **"Realizado 2026" mistura realizado e projeção.** Com a projeção out–dez carregada, o card principal e o rótulo
   "Realizado 2026" somam jan–set contábil + out–dez projetado, e o "Orçado 2026 até SET" vira o ano cheio. O gestor não
   vê, num relance, quanto já foi gasto de fato nem quanto se projeta fechar.
2. **Faltam os indicadores executivos**: desvio do realizado contra o orçado *no mesmo período*, % de execução do orçado
   do ano e projeção de fechamento contra o orçado. Hoje existem "Variação" (contra a base escolhida) e "anualizado"
   (projeção linear, que não é a projeção do gestor).
3. **Cards concorrentes**: na comparação orçado × realizado aparecem base, principal, variação, média mensal, anualizado e
   orçado do ano — seis cards do mesmo tamanho, sem ordem de importância.
4. **Onde estão os desvios** só aparece abrindo a tabela nível a nível; não há um resumo das maiores diferenças por conta
   e por centro de custo (com empresa) que leve direto ao "por quê?" e ao Rastro.
5. **Composição por tipo** (OPEX, CAPEX, Pessoal) mostra o valor, mas não a participação no total.

### Redundância e excesso de controles
6. Dois botões de limpar (`Resetar filtros` na barra e `Limpar tudo` na linha de filtros ativos) com o mesmo efeito.
7. A linha "Filtros ativos (guardados da sua última visita)" é longa; os chips não se removem individualmente (só a conta);
   o filtro de conta fica numa segunda linha separada; o CC aparece pelo código (o resto do Painel usa o nome).
8. Cabeçalho só com a saudação: não diz exercício, data do realizado nem escopo (todos os CCs × "meus CCs").

### Consistência visual
9. Cards de KPI com sombra, borda superior e "levantar" no hover — decoração sem função num número que não é clicável.
10. Coluna TOTAL do comparativo mensal soma a projeção dentro da barra azul do realizado, enquanto nos meses ela aparece
    empilhada em vermelho; a legenda da projeção diz sempre "out–dez", mesmo quando o realizado fecha em outro mês.
11. Sem estilo de foco visível em botões e links (só nos campos) — navegação por teclado difícil de acompanhar.

### Feedback
12. Mudança de filtro só esmaece o corpo (`is-loading`); não há aviso para o leitor de tela além de `aria-busy`.
13. Quando um indicador não pode ser calculado (sem orçado do ano, vários anos somados, projeção incompleta), nada
    explica por quê.

## O que muda (etapa 2)

- **Cabeçalho**: saudação mantida; linha de contexto com ciclo, realizado até o último mês fechado e escopo.
- **Filtros**: mesma disposição (linha 1 Empresa · Área · CC · Tipo; linha 2 Ano · Mês · Pacote); linha única "Filtros
  ativos" com chips removíveis (×), incluindo a conta; CC pelo nome; um só "Resetar filtros" (o da barra).
- **Execução do orçamento** (novo bloco de KPIs, `overview.execution`): Orçado do ano · Realizado até o mês fechado (só
  contábil, sem projeção) · Desvio no mesmo período (R$ e %) · % de execução do orçado do ano · Projeção de fechamento
  (realizado + projeção do gestor) contra o orçado. A projeção só aparece quando cobre **todos** os meses que faltam em
  **todas** as empresas do recorte; senão o card diz "Indisponível" e o motivo. Nada é estimado.
- Os cards atuais continuam quando a comparação é outra (ano anterior; orçamento 2027 × realizado 2026); na comparação
  orçado × realizado, o bloco novo substitui os cards repetidos.
- **Composição por tipo**: participação no total ao lado do valor.
- **Maiores desvios** (novo bloco, abaixo dos existentes; `GET /dashboard/deviations`): conta × CC, por CC e por conta,
  com empresa, valores, desvio em R$ e %, marcação "fora da faixa" (limiares do ciclo) e ações filtrar / por quê? / ver
  rastro. Usa o realizado contábil × orçado no mesmo período quando há execução; senão, a série principal × a base do Painel.
- **Comparativo mensal**: projeção empilhada também na coluna TOTAL; legenda com os meses reais da projeção.
- **Acessibilidade**: foco visível em botões e links; ▲/▼ + texto ("acima/abaixo do orçado") além da cor; status de
  carregamento anunciado (`aria-live`).
- **Visual**: KPIs numa faixa com divisórias finas, sem sombra nem hover; chips com 8 px.

## O que não muda, e por quê

- Arquitetura, regras de negócio, banco (sem migração), permissões, importações, auditoria e o contrato de
  `overview?figures=true` (só chaves novas).
- Comparativo mensal continua o primeiro visual; tabela, acumulado, maiores CCs, "Organizar painel", Critérios,
  Orçamento em construção, Perguntas e blocos da base continuam — pedido explícito da gestão.
- Cores (verde = orçamento, azul = realizado, roxo = ano anterior, vermelho = projeção); sem laranja e sem degradê.
- Anualização linear continua só como referência (rótulo "projeção linear"); a "Projeção de fechamento" é só a do gestor.
- Nenhuma biblioteca de gráfico nova.

## Etapa 3 — demais telas (10/10/2026)

> Backup antes da mudança: tag `backup-antes-etapa3-2026-10-11`. Prioridade pelo uso dos gestores: orçamento OPEX do CC,
> Justificativas, Perguntas, Apontamentos, Análise, Rastro, Consolidação, Pessoal/CAPEX do CC; admin só pelo CSS comum.

### Diagnóstico

1. **Laranja em toda parte**: tom `warn` (selos "Enviado", "Em análise", "Aviso", "Tipo 1"…, alertas, cards "proposto"),
   `--series-2` nos gráficos (CAPEX/encargos), barra de situação e figuras da Análise (`analytics.py`: ano anterior e CAPEX
   laranja, "Enviado" laranja, orçamento em verde `#1baf7a` e realizado em azul `#2a78d6` fora da paleta).
2. **Cor semântica trocada**: "2027 proposto/orçado/solicitado/simulado" em azul (cor do realizado) nos gráficos de pacote,
   CAPEX, pessoal, simulação e no fallback do card "Orçamento em construção"; todo aumento em vermelho mesmo dentro da faixa.
3. **Valores abreviados nos cards** (`fmtCompact`: "R$ 13,7 mi") no OPEX, Pessoal, CAPEX, Consolidação, Análise e simulação;
   variação só em % (sem a diferença em R$) nos cards, na coluna "Var." da visão por conta e nas listas de justificativas.
4. **Caixas demais**: cada justificativa, pergunta, pacote do OPEX e CC da fila de Apontamentos numa caixa com borda; cards
   e indicadores com sombra e "levantar" no hover; selos em pílula; busca em pílula com sombra.
5. **Filtros**: sem a linha "Filtros ativos" do Painel — o "Resetar filtros" ficava solto no fim dos selects, sem dizer o que
   estava aplicado; busca não aparecia como filtro.
6. **Tabelas**: cabeçalho cinza claro em caixa alta fina (só a do Painel estava em negrito); sem zebra; colunas de filial e
   fornecedor da grade mensal estreitas demais; botões de ícone (÷12, ⇄, ✕) sem rótulo acessível.
7. **Celular**: indicadores um por linha em cartões altos (a regra de 2 por linha era sobrescrita); o "por pacote" do OPEX
   estourava 22 px a 390 px.
8. **Menu**: dois grupos ("Orçamento" com 9 itens misturando o dia a dia e a Controladoria; "Administração" com 8).

### O que mudou

- **CSS comum** (`styles.css`, bloco "Etapa 3" + tokens): sem laranja (`--warn*` neutros, `--info` ardósia, `--series-2`
  ardósia, barra de situação neutra); selos retangulares de 8 px, "atenção" em contorno cinza; avisos com filete; cards e
  indicadores sem sombra; cabeçalho de tabela em negrito com linha de base forte, zebra sutil, dinheiro tabular; vazio e
  carregando sem caixa (`role="status"`); listas com filetes; foco visível em `summary`/`[tabindex]`; título de página 30 px.
- **FilterBar**: linha "Filtros ativos" (chips removíveis + "Resetar filtros (n)") automática no desktop; `ActiveFilters`
  exportado para Análise e Rastro (chips de empresa, área, CC, tipo, ano, mês, pacote, série, comparação, trilha).
- **Indicadores**: `Stat tone="budget"` (filete verde-água) para o valor do orçamento; valores por extenso; variações com
  "+R$ x (+y%)". No celular, uma linha por indicador.
- **OPEX do CC**: contexto do cabeçalho (OPEX 2027 · CC · empresa · gestor · versão · prazo); cards por extenso; "por pacote"
  com proposto em verde e nota "% (R$)"; linha do orçado em verde; coluna "Variação" com % e R$ (vermelho só fora da faixa);
  mensal com centavos; navegação de pacotes plana com valor por extenso; filial/fornecedor mais largos; rótulos nos ícones.
- **Justificativas / Perguntas**: listas em linhas; situação com ponto + texto (como o "Por que mudou?"); variação neutra com
  ▲/▼ e R$, vermelha só fora da faixa; pergunta mostra "valor na pergunta × atual" com a diferença em R$ e %; busca vira chip.
- **Apontamentos**: fila de CCs e regras sem caixas; avisos sem cor; contexto curto com a versão.
- **Análise**: cards por extenso, variações em R$ com % no rodapé, sem vermelho/verde por sinal; figuras na paleta do app.
- **Consolidação, Pessoal, CAPEX, simulação**: cards por extenso, orçamento em verde, composição sem laranja.
- **Menu** (sem remover rota): Acompanhamento (Painel, Análise, Rastro, Consolidação) · Gestão do orçamento (Justificativas,
  Perguntas, Apontamentos, Contratos PJ) · Controladoria (Validação, Importação, Situação da base, Premissas de pessoal,
  Ciclo) · Administração (Cadastros, Áreas e setores, Usuários, Auditoria); a folha "Mais" do celular mostra os mesmos grupos.

### O que não mudou, e por quê

- Regras de negócio, permissões, auditoria, importações, cálculos e APIs (nenhuma rota nova; backend só trocou cores das
  figuras da Análise). Nenhuma função removida.
- Rótulos em cada ponto dos gráficos mensais em SVG (`MonthlyBars`) com 3 séries ainda se sobrepõem em meses próximos — é a
  regra "maior e menor de cada série"; mudar exige decidir quais séries rotular (pendente, pedir à gestão).
- Telas de administração receberam só o CSS comum (sem revisão tela a tela).

