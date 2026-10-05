# ATEM — Sistema de Planejamento e Orçamento 2027

Aplicação web que substitui o ciclo orçamentário baseado em planilhas (OPEX por pacotes GMD, CAPEX e Pessoal),
com importação versionada, workflow de aprovação, auditoria e comparação 2025 R → 2026 R/O → 2027 P.

| Documento | Conteúdo |
|---|---|
| [`docs/01-analise-templates.md`](docs/01-analise-templates.md) | Análise aba a aba dos 3 templates + cartilha: colunas, fórmulas, listas, regras, problemas encontrados |
| [`docs/02-especificacao-tecnica.md`](docs/02-especificacao-tecnica.md) | Arquitetura, modelo de dados, regras (fórmula → backend), workflow, importação, UX, roadmap |

## Estado atual — Fase 1 (Fundação) ✅

| Item | Situação |
|---|---|
| Modelo de dados completo (46 tabelas: org, contas/pacotes GMD, ciclo/versões, fatos, OPEX, CAPEX, pessoal, importação, auditoria) | ✅ migração Alembic `0001` |
| Autenticação JWT + perfis (ADMIN, CONTROLLER, MANAGER, PACKAGE_MANAGER, HR, VIEWER) + escopo por CC | ✅ |
| Cadastros com CRUD auditado (empresas, filiais, CCs, contas, pacotes, detalhamentos, listas, contratos, gestores de pacote) | ✅ |
| Ciclo 2027, parâmetros (limites de alerta, fatores de cálculo), versões | ✅ |
| Importação: upload → detecção de layout → validação → prévia → relatório de erros `.xlsx` → confirmação → nova versão | ✅ |
| Layouts: BD-Novo dos templates, Realizado (largo), SAP KSB1 (`.csv`/`.xlsx`), Orçamento de referência, Quadro de funcionários, Premissas macro | ✅ |
| Regras de cálculo puras (viagem, evento, CAPEX, projeção de pessoal, what-if CLT×PJ, desligamentos) + endpoints de simulação | ✅ |
| Seed com domínios extraídos dos templates (90 contas, 15 pacotes, 107 valores de listas, tarifas de viagem, gestores de pacote) | ✅ |
| 33 testes automatizados (regras, importação ponta a ponta, RBAC, auditoria) | ✅ |
| Interface web (React): login, painel, importação com prévia/erros/confirmação, cadastros, ciclo e parâmetros, usuários, auditoria | ✅ |
| Proteção contra reimportação, comparação com a base vigente, modos Atualizar/Substituir, lista de erros na tela | ✅ |
| Painel: realizado ano anterior × ano de referência (mesmo período), anualizado, pacotes, rankings, qualidade da base | ✅ |
| Telas de OPEX (histórico e preenchimento) | Fase 2 |

A interface fica na raiz do domínio; a API está documentada em `/api/docs` (Swagger).

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

Testes (requerem um PostgreSQL de teste):

```bash
DATABASE_URL="postgresql+psycopg://postgres@localhost:5432/atem_test" pytest
ruff check . && ruff format --check .
```

## Deploy no Railway

1. **New Project → Deploy from GitHub repo** apontando para este repositório (o `railway.json` usa o `Dockerfile` da raiz).
2. **+ New → Database → PostgreSQL** no mesmo projeto.
3. No serviço da aplicação, em **Variables**:

| Variável | Valor |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (referência ao plugin) |
| `SECRET_KEY` | valor aleatório com 32+ caracteres (`python -c "import secrets;print(secrets.token_urlsafe(48))"`) |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | primeiro administrador (criado no primeiro start) |
| `CORS_ORIGINS` | domínio do app (ou `*` enquanto só houver a API) |

4. **Volume**: em *Settings → Volumes*, monte um volume em `/data` (arquivos enviados ficam em `/data/uploads`).
5. *Settings → Networking → Generate Domain*. O start executa `alembic upgrade head`, o seed e sobe o Uvicorn; healthcheck em `/api/health`.

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
docs/                análise e especificação
```
