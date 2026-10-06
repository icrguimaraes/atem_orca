# ATEM — Sistema de Planejamento e Orçamento 2027

Aplicação web que substitui o ciclo orçamentário baseado em planilhas (OPEX por pacotes GMD, CAPEX e Pessoal),
com importação versionada, workflow de aprovação, auditoria e comparação 2025 R → 2026 R/O → 2027 P.

| Documento | Conteúdo |
|---|---|
| [`docs/01-analise-templates.md`](docs/01-analise-templates.md) | Análise aba a aba dos 3 templates + cartilha: colunas, fórmulas, listas, regras, problemas encontrados |
| [`docs/02-especificacao-tecnica.md`](docs/02-especificacao-tecnica.md) | Arquitetura, modelo de dados, regras (fórmula → backend), workflow, importação, UX, roadmap |
| [`docs/design-system.md`](docs/design-system.md) | Design system Coral Stay (cores, tipografia, componentes) e como foi aplicado |

## Estado atual — 5 fases do roadmap concluídas ✅

| Fase | O que entrega | Situação |
|---|---|---|
| 1 — Fundação | Modelo de dados (47 tabelas, migrações `0001`–`0004`), JWT + perfis (ADMIN, CONTROLLER, MANAGER, PACKAGE_MANAGER, HR, VIEWER) com escopo por CC, cadastros com CRUD auditado, ciclo/parâmetros/versões, importação versionada (upload → detecção → validação → prévia → confirmação), painel do realizado | ✅ |
| 2 — OPEX | Orçamento por centro de custo e pacote GMD (viagens, eventos, grade mensal), histórico por conta com alertas e justificativas, workflow (envio → análise → aprovação → consolidação) e validação dos pacotes Tipo 1; importação do template OPEX preenchido (todas as abas, linha a linha) | ✅ |
| 3 — CAPEX | Solicitações (projeto ou aquisição) com itens, valor unitário × quantidade e cronograma; pendências críticas bloqueiam envio/aprovação; catálogo de ativos sugere a conta; importação do template CAPEX | ✅ |
| 4 — Pessoal | Quadro por CC com uma ação por colaborador (promover, reajuste, desligar, transferir, admissão), vagas, custo = salário × reajuste × multiplicador do contrato (CLT 1,8; PJ sem), validação GMD do pacote Pessoas, cenários e simulação (what-if) | ✅ |
| 6 — Análise (Plotly) | Dashboard FP&A interativo: KPIs (realizado 2025, orçado 2026, orçamento 2027, variações, módulos, % preenchido/aprovado), evolução mensal, comparativo por dimensão com drill-down (empresa → diretoria → CC → conta → mês), variação R$/%, ranking, composição, análise por CC e andamento; figuras geradas em Python (plotly) a partir da mesma base da consolidação | ✅ |
| 5 — Consolidação | Painel OPEX + CAPEX + Pessoal × realizado, pontos de atenção, congelamento de versão (fotografia imutável), revisão (1.1 / 2.0) e exportação Excel (resumo, carga SAP chave × mês, consolidado, variações, detalhes por módulo, status) | ✅ |

Também: bases carregadas com exclusão por escopo, por módulo ou total; comparação entre versões (1.0 × 1.1); exportação do **template preenchido** por CC (OPEX e CAPEX, reimportável); alteração de senha e limite de tentativas de login; design system Coral Stay (`docs/design-system.md`); 60 testes de backend (regras, importação ponta a ponta, workflow, RBAC, consolidação, análise, ida e volta dos templates) e 22 testes de interface Playwright (`frontend/e2e/`, desktop e celular).
A interface fica na raiz do domínio; a API está documentada em `/api/docs` (Swagger).

### Próximos passos sugeridos (fora do roadmap original)

- Carga no SAP a partir da aba **Carga SAP** (hoje via Excel; integração direta quando houver acesso).
- Rateio de CSC/BackOffice entre empresas (matriz de rateio da contabilidade) — ver decisão 6 em `docs/02`.
- Notificações por e-mail (prazos, ajuste solicitado, envio recebido).
- Confirmar com a contabilidade as contas de pessoal da consolidação (parâmetros `personnel.*_account`).

### Logo do grupo

A logo oficial está em `frontend/public/brand/` (`logo.png`, `logo-dark.png` para o tema escuro e `favicon.png`); o app a usa na
barra lateral, no celular e no login. Chegando o arquivo vetorial, substitua os três mantendo os nomes.

### Testes de interface (Playwright)

Rodam contra um app no ar (local ou Railway) com um usuário válido:

```bash
cd frontend && E2E_BASE_URL=http://localhost:8077 E2E_EMAIL=... E2E_PASSWORD=... npm run e2e
```

As credenciais não têm padrão no código (o teste falha sem `E2E_EMAIL`/`E2E_PASSWORD`). `smoke.spec.ts` abre todas as páginas em desktop e 390px (sem erro de console nem rolagem horizontal);
`fluxos.spec.ts` cobre drill-down da análise, exportação da consolidação, download do template e alteração de senha.

## Rodando localmente

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # ajuste DATABASE_URL, SECRET_KEY e ADMIN_PASSWORD
alembic upgrade head
python -m app.seed              # idempotente
uvicorn app.main:app --reload   # http://localhost:8000/api/docs
```

Frontend (outro terminal; faz proxy de `/api` para `localhost:8000`):

```bash
cd frontend && npm install && npm run dev   # http://localhost:5173
```

Testes (requerem um PostgreSQL de teste; `scripts/dev-db.sh` sobe um local em `/tmp:5433` com os bancos `atem` e `atem_test`):

```bash
scripts/dev-db.sh
cd backend && pytest                      # usa postgresql+psycopg://postgres@/atem_test?host=/tmp&port=5433 por padrão
ruff check . && ruff format --check .
```

## Deploy no Railway

1. **New Project → Deploy from GitHub repo** apontando para este repositório (o `railway.json` usa o `Dockerfile` da raiz).
2. **+ New → Database → PostgreSQL** no mesmo projeto.
   O CI (`.github/workflows/ci.yml`) roda ruff, pytest (com PostgreSQL), verificação das migrações e o build do frontend a
   cada push; configure o Railway para publicar só após o CI verde (*Settings → Deploy → Wait for CI*).
3. No serviço da aplicação, em **Variables**:

| Variável | Valor |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (referência ao plugin) |
| `SECRET_KEY` | valor aleatório com 32+ caracteres (`python -c "import secrets;print(secrets.token_urlsafe(48))"`) |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | primeiro administrador (criado no primeiro start) |
| `CORS_ORIGINS` | domínio do app (ou `*` enquanto só houver a API) |

4. **Volume**: em *Settings → Volumes*, monte um volume em `/data` (arquivos enviados ficam em `/data/uploads`).
5. *Settings → Networking → Generate Domain*. O start executa `alembic upgrade head`, o seed e sobe o Uvicorn; healthcheck em `/api/health`.

### Backup e restauração

- **Banco**: o PostgreSQL do Railway tem backups automáticos (*Database → Backups*); para um backup manual use
  `railway connect Postgres` e `pg_dump -Fc -f atem.dump`; restauração com `pg_restore -d $PGDATABASE --clean atem.dump`.
- **Arquivos enviados** (`/data/uploads`): são os `.xlsx` importados, guardados para auditoria. O banco não depende deles
  para funcionar (as linhas lidas ficam em `import_rows`). Para copiar: `railway ssh` → `tar czf - /data/uploads > uploads.tgz`.
- **Teste de restauração**: suba um banco vazio, `pg_restore`, inicie o app (as migrações não alteram nada se já estiver
  no `head`) e confira `/api/health` e o painel. Faça isso uma vez antes do fechamento do orçamento.

### Worker de importação em serviço separado

O worker de importação roda em thread no mesmo container. Para separar: crie um segundo serviço com a mesma imagem,
comando `python -m app.worker`, e defina `RUN_IMPORT_WORKER=false` no serviço web.

## Fluxo de importação (Swagger)

1. `POST /api/v1/auth/token` (botão **Authorize**).
2. `POST /api/v1/imports` com o arquivo (tipo opcional — detecção automática; para realizado sem data no cabeçalho informe `reference_year`).
3. `GET /api/v1/imports/{id}` até `status = VALIDATED` → `GET /preview` e `GET /errors.xlsx`.
4. `POST /api/v1/imports/{id}/confirm` → `COMPLETED`; histórico em `GET /api/v1/dataset-versions`.

> As planilhas reais não são versionadas (`.gitignore`): contêm dados pessoais e financeiros. Os testes geram
> planilhas sintéticas no mesmo layout (`backend/tests/builders.py`).

## Estrutura

```
backend/
  app/
    models/          entidades SQLAlchemy (schema completo das 5 fases)
    domain/rules/    regras de negócio puras (fórmulas Excel → Python)
    imports/         parsers por layout, detector, validação contra cadastros, carga versionada, worker
    api/v1/          rotas REST
    seed_data.py     domínios extraídos dos templates/cartilha
  alembic/           migrações
  tests/
frontend/            React + Vite (build copiado para backend/frontend_dist no Docker)
docs/                análise, especificação (regras por fase nas seções 12–15) e design system
scripts/             dev-db.sh (PostgreSQL local para desenvolvimento e testes)
```
