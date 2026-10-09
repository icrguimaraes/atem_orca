# ATEM · Orçamento 2027 — guia para sessões de desenvolvimento

Sistema web (FastAPI + SQLAlchemy 2 + PostgreSQL 16 | React 18 + Vite) que substitui os templates Excel do ciclo
orçamentário da ATEM. Leia `README.md` (estado e deploy), `docs/02-especificacao-tecnica.md` (regras por fase,
seções 12–15) e `docs/design-system.md` (visual) antes de alterar algo.

## Comandos

```bash
scripts/dev-db.sh                                   # PostgreSQL local (/tmp:5433; bancos atem e atem_test)
cd backend && pip install -r requirements-dev.txt
cd backend && alembic upgrade head && python -m app.seed   # DATABASE_URL, SECRET_KEY, ADMIN_EMAIL, ADMIN_PASSWORD no ambiente
cd backend && pytest -q                              # suíte completa (~1 min); conftest usa atem_test por padrão
cd backend && ruff check . && ruff format --check .  # obrigatório antes de commitar
cd frontend && npm ci && npm run build               # tsc -b + vite; o Docker copia dist para backend/frontend_dist
cd backend && FRONTEND_DIST=../frontend/dist uvicorn app.main:app --port 8077   # app completo local
cd frontend && E2E_BASE_URL=http://localhost:8077 E2E_EMAIL=... E2E_PASSWORD=... npm run e2e   # Playwright (app no ar)
```

## Convenções

- Idioma: código em inglês, textos de interface, mensagens de erro, commits e docs em pt-BR.
- Dinheiro é `Decimal` (`domain/rules/common.money`), nunca float; a API devolve valores como string decimal.
- Regras de negócio puras ficam em `app/domain/rules/` (sem banco); orquestração em `app/services/`; rotas finas em `app/api/v1/`.
- Toda mutação relevante grava em `audit_logs` via `services.audit.record` (before/after).
- Versão do orçamento: `services.opex.context()`; `ctx.frozen` = só leitura (verificar em qualquer nova mutação).
- Importação: parser (`imports/parsers`) → `resolver.validate` → `compare` → `loaders` (chamados pelo worker). Novos tipos
  entram em `DatasetType`, `detector.PARSERS/AUTO_ORDER`, `resolver.validate`, `compare.compare`, `loaders.LOADERS` e nos
  rótulos do frontend (`labels.ts`).
- Esquema: alterar o modelo **e** criar migração Alembic numerada (`alembic/versions/000N_*.py`); conferir drift com
  `alembic.autogenerate.compare_metadata` (ver testes anteriores) — up e down devem funcionar.
- Novos parâmetros do ciclo entram em `seed_data.CYCLE_PARAMETERS` (o seed acrescenta aos ciclos existentes) e em
  `PARAM_LABELS` no frontend.
- Frontend: tokens de cor/fonte em `styles.css` (`:root`, claro e escuro); gráficos em `components/charts.tsx` com a paleta
  `SERIES`; coral só em ações primárias. Toda tabela nova vai dentro de `.table-wrap`; conferir celular (390px) sem overflow.
- Gráficos analíticos: figuras Plotly são montadas **no backend** (`services/analytics.py`, uma base filtrada por request,
  formatação pt-BR em rótulos/tooltips) e renderizadas por `components/PlotlyChart.tsx` (bundle `plotly.js-basic-dist-min`:
  barras, linhas e pizza; carregado só na Análise e no Painel). Novos gráficos: nova função `fig_*` + chave em `dashboard()['figures']`.
- Painel (`pages/Painel2.tsx`, rota `/`; `/painel-2` redireciona): gráficos em Plotly com os números de `/dashboard/overview`,
  figuras em `services/painel_figures.py` (`overview?figures=true`, filtro extra `account_id`); tooltip próprio via `meta.tooltip`
  + linhas no fim do `customdata`; clique nos visuais filtra (o visual clicado destaca em vez de se filtrar — séries sem o
  próprio filtro e `heatmap_all`; novo clique desmarca) e "Limpar filtros" reseta. O Painel antigo em SVG (`Home.tsx`) saiu em
  07/10/2026; os blocos que não são gráfico (tarefas do gestor, validações GMD, quadro de pessoal e premissas) estão em
  `components/PainelBlocks.tsx`. Cadastros, contratos, qualidade da base, importações e versões ficam na página de consulta
  "Situação da base" (`pages/BaseStatus.tsx`, rota `/base`, só Controladoria).
- Justificativas (08/10/2026, "justificar tudo"): toda conta OPEX orçada (ou zerada com histórico ≥ `alert.min_relevant_amount`),
  toda movimentação de pessoal e toda solicitação de CAPEX precisam de justificativa — críticas em Apontamentos e bloqueiam o envio.
  Tela `pages/Justifications.tsx` (`/justificativas`) sobre `services/justifications.py` + `api/v1/justifications.py` (salvar grava
  auditoria e, se faltava, a correção em Apontamentos; `export.xlsx`). O template OPEX exportado leva a justificativa da conta
  nas linhas sem justificativa própria.
- Defesa do orçamento (08/10/2026): botão "por quê?" em cada linha da tabela do Painel (`components/WhyPanel.tsx`: resumo no
  hover, painel lateral no clique) sobre `GET /dashboard/why` (`api/v1/defense.py`: orçamento do ciclo × realizado do ano anterior
  anualizado, decomposição por conta/CC, justificativas, alertas). Pergunta-se sobre o **lançamento** (09/10/2026): o painel lista as
  linhas OPEX do recorte (`GET /dashboard/why/lines`, maiores primeiro, busca) e as movimentações/CAPEX, cada uma com "Questionar";
  também na grade de linhas do orçamento do CC (`permissions.ask`). `BudgetQuestion` (migrações 0007 e 0012: `item_type` + FK do
  item com SET NULL + `item_snapshot`) vai ao gestor do CC do lançamento (e do CC atual, se foi movido) ou à Controladoria; perguntas
  antigas por recorte continuam valendo; auditoria — página `pages/Questions.tsx` (`/perguntas`, valor na pergunta × atual).
- Contratos PJ (08/10/2026, confidencial; `docs/06-contratos-pj.md`): página `pages/ContratosPj.tsx` (`/pj`) sobre
  `domain/rules/pj.py` (CNPJ inclusive alfanumérico, bonificação proporcional) + `services/pj.py` + `api/v1/pj.py`
  (migração 0009; 0011: nada obrigatório — vazios viram pendência `missing`, "Falta preencher"). Acesso por flags do
  usuário, não por perfil: `users.can_view_pj`/`can_view_all_pj` ("Vê contratos PJ: Não / Da área / Todos" no editor de Usuários; `/auth/me` expõe `pj_access`); "Da área" = CCs das áreas dos CCs da própria
  pessoa, mesmo com perfil global. Toda rota exige `core.deps.require_pj`; auditoria sem valores em R$ e escondida de quem
  não tem acesso (`api/v1/audit_logs.py`).
- Exportação de template (`services/template_export.py`) deve continuar legível pelos parsers (`tests/test_template_export.py`
  faz a ida e volta); A1 de `Instruções` leva `EXPORT_MARKER`, que faz a reimportação substituir todos os lançamentos do CC.
- Modais usam portal (`ui.Modal`); toda tabela dentro de `.table-wrap` — o smoke E2E falha com rolagem horizontal a 390px.
- O Painel é único: período = anos somados × meses × tipos de orçamento (`api/v1/dashboard._period` e `Facts`);
  verde = orçado/orçamento (inclui o orçamento proposto do ano do ciclo, via consolidação), azul = realizado, roxo = ano anterior.
- Filtros de página sempre via `components/FilterBar` (desktop: selects; celular: grade + folha "Aplicar"); opções podem depender do rascunho (`options: (draft) => …`).
  Estado dos filtros com `usePersistentState` (`src/persist.ts`: guardado no navegador e apagado no login/logout) e botão
  "Resetar filtros" via `onReset`/`resetCount` do FilterBar.
- Celular: navegação na barra de abas inferior (`Layout.MobileTabs`, lista `TABS`) + folha "Mais"; nova página entra em `NAV` e aparece em "Mais".
- Testes usam planilhas sintéticas de `tests/builders.py`. **Nunca** commitar `.xlsx` reais (dados pessoais) — já no `.gitignore`.
- Branch de trabalho: `claude/atem-budget-planning-2027-1ril6s`; o Railway publica a cada push. Não abrir PR sem pedido.

## Dados sensíveis

Não pedir nem registrar `SECRET_KEY`/`ADMIN_PASSWORD`; os templates reais ficam fora do repositório.
