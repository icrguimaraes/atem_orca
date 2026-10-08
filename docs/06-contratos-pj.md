# Especificação — Contratos PJ

Controle confidencial dos prestadores contratados como pessoa jurídica (PJ) pela Controladoria: valor mensal,
bonificação anual, tempo de casa e bonificação devida no ano. Nasceu no ATEM Movimentação de Pessoal (handoff de
08/10/2026) e passou a viver aqui, adaptado ao modelo do Orçamento (a "estrutura" é o **centro de custo**, que já leva
Área → Setor). Migração **0009**.

## 1. Acesso

Nível por pessoa, escolhido pelo Administrador em **Usuários → Editar** ("Vê contratos PJ": **Não / Da área /
Todos**); toda troca fica na auditoria. Vale para qualquer perfil: um Administrador sem acesso pode concedê-lo, a si ou
a outros, mas não vê os dados antes disso. Nomes não ficam no código.

| Nível | Campos em `users` | O que vê e faz |
|---|---|---|
| Não | `can_view_pj = false` | nada: toda rota `/api/v1/pj/*` responde 403 "Sem acesso aos contratos PJ" (`core.deps.require_pj`) |
| Da área | `can_view_pj = true`, `can_view_all_pj = false` | só contratos dos CCs das **áreas** dos próprios CCs (sempre, qualquer que seja o perfil) |
| Todos | `can_view_pj = true`, `can_view_all_pj = true` | todos, inclusive os sem centro de custo |

- `PATCH /users/{id} {can_view_pj, can_view_all_pj}` (e `POST /users` aceita os dois); "Não" limpa também o "Todos".
  `/auth/me` e `GET /users` devolvem `can_view_pj`, `can_view_all_pj` e `pj_access` (`NONE | AREA | ALL`).
- **Escopo "Da área"** (`services.pj.scope_for` / `area_cost_center_ids`): CCs próprios da pessoa (gestor do CC +
  `user_scopes` por CC, empresa ou área — `core.deps.own_cost_center_ids`, sem o "vê tudo" dos perfis globais) → áreas
  (`departments`) desses CCs → **todos os CCs dessas áreas**. Administrador/Controladoria com "Da área" e sem CC próprio
  não veem nada.
- Com "Da área": lista, resumo, filtros, `/pj/options` (só os CCs do escopo), fotos e Excel trazem só o escopo; get,
  edição, arquivamento e foto de contrato fora dele respondem **404** (não revela que existe). Ao criar ou editar, o CC
  é obrigatório (422 "Informe o centro de custo…") e precisa estar no escopo (403 "Centro de custo fora da sua área…").
- Menu "Contratos PJ" (grupo Orçamento) e a rota `/pj` só aparecem com acesso (`NAV` filtra pela flag `pj` do item e
  `Protected pj` na rota).
- Auditoria (`/audit-logs`, `entity_type = "pj_contract"`): sem acesso, nenhum registro; "Da área", só os dos
  contratos do escopo (inclusive arquivados); "Todos", toda.

## 2. Modelo (migração 0009)

`pj_contracts`: `name` (pessoa), `company_name` (razão social), `cnpj` (14 posições sem pontuação, **sem bloqueio de
duplicidade**), `role` (função, texto livre), `cost_center_id` (opcional; obrigatório para "Da área"), `email`,
`phone`, `monthly_value` (Numeric 18,2, obrigatório), `annual_bonus` (Numeric 18,2, opcional), `start_date`
(admissão), `end_date` (término, opcional), `notes`, `photo_blurred`, `archived_at` (exclusão lógica),
`created_by`/`updated_by` e carimbos. `pj_photos`: foto por contrato (JPEG/PNG/WebP até 400 KB; a tela recorta e grava
JPEG 280×280). `users.can_view_pj` / `users.can_view_all_pj`.

Situação **derivada**: Encerrado depois do dia do término; até lá (e sem término), Ativo. "Encerrar" = informar a data
de término. "Arquivar" tira o contrato da lista e dos totais (para cadastro errado); o registro continua na auditoria.

## 3. Regras (puras, em `app/domain/rules/pj.py`)

- **CNPJ**: aceita com ou sem máscara; valida os dígitos verificadores (módulo 11, pesos 5…2 e 6…2), recusa
  sequências repetidas; aceita também o **CNPJ alfanumérico** (12 posições `[0-9A-Z]` + 2 dígitos, valor = ASCII − 48,
  em vigor desde 07/2026). Grava sem pontuação; mostra `00.000.000/0000-00`.
- **Tempo de casa**: meses completos da admissão até hoje (ou até o término, se encerrado), em anos e meses.
- **Bonificação devida no ano** (confirmada pelo dono do produto em 08/10/2026; tudo em `bonus_months`/`bonus_due`
  para trocar num lugar só; a tela mostra a fórmula):
  - meses no ano: admissão antes do ano = 12; admissão no ano = a partir do mês da admissão, **em qualquer dia**
    ("livre no mês"); término no ano = até o mês do término (inclusive); término antes do ano ou admissão depois
    dele = 0; limite de 12;
  - devida = bonificação anual × meses ÷ 12, arredondada aos centavos (meio para cima, `Decimal`).

## 4. Auditoria (sem valores em R$)

`CREATE`, `UPDATE` (só o que mudou), `ARCHIVE`, `PHOTO_SET`, `PHOTO_DELETE`, `EXPORT` e `PJ_VIEW` (consulta à lista ou a
um contrato, no máximo uma vez a cada 30 min por pessoa, em memória do processo). Valor mensal e bonificação nunca vão
para a auditoria: no `UPDATE` aparecem como "(sigiloso) → (alterado)"; na criação, "(sigiloso)" se preenchidos.

## 5. API (`/api/v1/pj`, tudo exige `can_view_pj` e respeita o escopo)

Valores em string decimal; datas `"AAAA-MM-DD"`; `year` padrão = ano corrente.

```text
GET    /pj?year=&status=ACTIVE|ENDED&cost_center_id=&q=   → {year, items: [Contrato]} (ativos primeiro, por nome; q = nome, empresa ou CNPJ)
GET    /pj/summary?year=&cost_center_id=&q=               → {year, active, ended, monthly_total, annual_bonus_total, bonus_due_total}
GET    /pj/options                                        → {all, cost_centers: [{id, code, name, department, sector}] (só do escopo)}
GET    /pj/photos                                         → {"<id>": "data:image/jpeg;base64,..."} (não arquivados)
GET    /pj/export.xlsx?year=                              → Excel com todos os campos, área/setor e a bonificação do ano
GET    /pj/{id}?year=                                     → Contrato
POST   /pj {name, company_name, cnpj, role?, cost_center_id?, email?, phone?, monthly_value, annual_bonus?,
            start_date, end_date?, notes?, photo_blurred?}  → 201 Contrato
PATCH  /pj/{id} {mesmos campos, só os enviados}           → Contrato
DELETE /pj/{id}                                           → arquiva {id}
PUT    /pj/{id}/photo (multipart file)  · DELETE /pj/{id}/photo
```

`Contrato = {id, name, company_name, cnpj, cnpj_formatted, role, cost_center_id, cost_center, department, sector,
email, phone, monthly_value, annual_bonus, start_date, end_date, status, notes, has_photo, photo_blurred,
tenure: {years, months}, bonus: {year, months, due}}`.

Indicadores: ativos, total mensal e bonificação anual contam os contratos **ativos hoje**; a bonificação devida soma
todos os contratos com meses no ano escolhido (inclui os encerrados durante o ano). Filtros de CC e busca valem para os
indicadores; o de situação, só para a lista. Erros 422 em pt-BR ("CNPJ inválido", "Valor mensal não pode ser
negativo", "A data de término não pode ser anterior à admissão", "Centro de custo não encontrado" etc.).

## 6. Tela (`/pj`, `pages/ContratosPj.tsx` + `pj.css`)

Cabeçalho com o ano da bonificação, botão "Embaçar dados" (modo privacidade da página: embaça nomes, CNPJ, valores e
fotos — `.pii`/`.sens`), "Exportar Excel" e "Novo contrato"; cartões: contratos ativos, total mensal, bonificação anual
e bonificação devida no ano; filtros (FilterBar: situação, centro de custo com área; busca) com estado persistido e
"Resetar filtros"; a fórmula da bonificação em nota discreta. Lista em tabela (`.table-wrap`) no desktop e cartões no
celular: foto, nome, razão social e CNPJ, função e CC/área/setor, admissão com tempo de casa (e término), mensal,
bonificação anual, devida no ano com os meses contados e a situação. Clique abre a janela de edição (`ui.Modal`): todos
os campos, foto (incluir, recortar com `components/pj/PhotoEditor.tsx`, ajustar, remover, embaçar), CC obrigatório e
só da área para "Da área", valores no padrão "12.345,67", tempo de casa e devida no ano, "Arquivar" com confirmação.

Em **Usuários → Editar**, seção "Perfis": select "Vê contratos PJ: Não / Da área / Todos" (salvo pelo `PATCH
/users/{id}` ou já no `POST /users`); a lista mostra um selo "Contratos PJ: …" para quem tem acesso.

## 7. Testes

`tests/test_pj_rules.py` (CNPJ numérico e alfanumérico, máscara, inválidos; situação; tempo de casa; bonificação por
cenário; arredondamento) e `tests/test_pj.py` (todas as rotas sem acesso = 403, inclusive Administrador; níveis pela
API de usuários com auditoria; validação; CRUD + arquivar com auditoria sem valores; auditoria escondida sem acesso;
resumo, filtros e Excel; fotos; escopo "Da área" por área do CC, Controladoria com "Da área"; migração 0009 up/down com
`compare_metadata` sem drift).
