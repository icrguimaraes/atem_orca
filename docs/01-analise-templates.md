# 01 — Análise dos Templates Excel (Ciclo Orçamentário 2027)

Fontes analisadas:

| # | Arquivo | Abas | Papel no processo |
|---|---|---|---|
| A | `Template_OPEX 2027_Icaro Guimarães.xlsx` | 16 (1 oculta) | Orçamento de despesas por pacote GMD |
| B | `Template_CAPEX 2027 - ATEM_Icaro Guimarães.xlsx` | 5 (3 ocultas) | Solicitação de investimentos |
| C | `Template Orçamento Atem Pessoal CLT 2026 - Automação de dados.xlsx` | 2 (1 oculta) | Quadro de pessoal e movimentações |
| D | `CARTILHA_ORÇAMENTO_2027.pdf` | 14 slides | Conceitos, GMD, gestores de pacote, CSC |

Os templates são **personalizados por gestor** (a tabela de centros de custo de cada arquivo contém só o CC do gestor — no caso, `1050101011 – DADOS E PROJ. APLICADOS A CONTROLADORIA`, gestor Icaro Guimarães). A Controladoria distribui um arquivo por gestor, recebe de volta e consolida manualmente. O sistema substitui essa distribuição/consolidação.

---

## 1. Conceitos de negócio (Cartilha + aba Instruções)

| Conceito | Definição operacional |
|---|---|
| **Conta contábil (Conta do Razão)** | "O QUÊ" do gasto. Unidade principal do orçamento. Ex.: `6010301001 Hospedagem`. |
| **Centro de custo (CC)** | "ONDE/QUEM" gasta. Pertence a uma empresa, possui gestor responsável. |
| **Filial / Local de negócios** | Código de 4 dígitos (`0001 MANAUS`, `0009 BELEM`...). 16 filiais no template. |
| **Empresa** | Código SAP de 4 dígitos. `1001 = ATEM`, `2001 = REAM` (nomes `Ativo.1001`, `Ativo.2001`). Grupo: Atem, Ream, Navemazônia, AM Energia, Rodo Amazônia, Norte Construtora, DMN, Bioenergia. |
| **Chave orçamentária** | `EMPRESA-FILIAL-CC-CONTA` (ex.: `1001-0001-1050101011-6010301001`). É a granularidade de consolidação em todas as abas. |
| **GMD — Gestão Matricial de Despesas** | Cada gasto tem dois responsáveis: o **gestor do CC** (eixo vertical/área) e o **gestor do pacote** (eixo horizontal/conjunto de contas). |
| **Pacote** | Agrupamento de contas correlatas (Viagens, DTI, Jurídico...). Cada conta pertence a exatamente **um** pacote. |
| **Pacote Tipo 1** | Validação **formal e obrigatória** do gestor de pacote (Comunicação/MKT, Viagens, Pessoas, Serviços de Terceiros, Infraestrutura ATEM/REAM/NAVE). |
| **Pacote Tipo 2** | Gestor de pacote **consultivo** — comenta/orienta, sem bloqueio (Segurança/Seguros, Op. Financeiras, Tesouraria, Tributário, Consumo e Expediente, Jurídico ×3, DTI*, Comerciais ATEM/REAM/NAVE, Logística). |
| **OPEX / Custo / GGF / CAPEX** | Natureza do gasto. CAPEX: bem > R$ 1.200, vida útil > 12 meses, aquisição/criação/melhoria de ativo, reforma estrutural. |
| **CSC** | Funcionários que atuam para várias empresas são orçados **na ATEM**, no CC da área; rateio posterior pela contabilidade para CCs de BackOffice de cada empresa. |
| **Agrupamento DRE** | `Despesas`, `Resultado Financeiro`, `Sem Agrupamento` (CAPEX). |

> *Inconsistência detectada:* DTI aparece como Tipo 1 nas instruções do Excel e como Tipo 2 na cartilha. Nomes de gestores também divergem (Christiane **Ribeiro** × **Pinheiro**; Tesouraria: Alessandra Freitas × Christiane Pinheiro; Tributário: Alberto Gabriel × Alberto Silva). **Conclusão: tipo do pacote e gestores de pacote devem ser parâmetros por ciclo**, nunca fixos.

Regras de preenchimento extraídas das instruções:

1. Células **cinza** = entrada; **rosa** = fórmula.
2. Despesas **não recorrentes não devem ser orçadas**.
3. Se a área atua em outras filiais, orçar cada filial (inclusive novas).
4. Despesas migradas de outras empresas para ATEM devem ser orçadas se houver continuidade.
5. **Pessoal não está no template OPEX** — é elaborado exclusivamente pelo RH (template C).
6. Evitar orçar despesa sem histórico ou sem justificativa clara → regra de alerta.
7. Prazos: OPEX 09/10/2026; CAPEX 15/10/2026 → parâmetro de ciclo (`deadline` por módulo).

---

## 2. Template OPEX

### 2.1 Mapa de abas

| Aba | Estado | Função |
|---|---|---|
| `BD-Novo` | oculta | Base de cadastros (tabelas Excel `Centro_de_Custos`, `Filiais`, `Pacotes`) + listas de domínio + listas dinâmicas por pacote |
| `Instruções` | visível | Prazos, responsáveis, regras por pacote |
| `Realizado 2026` | visível | Tabela `Realizado_2026` (histórico informativo Jan–Ago/26) |
| `Resumo 2027` | visível | Consolidado mensal por pacote + realizado acumulado |
| `I - Viagens` … `XII - Comercial` | visíveis | 12 formulários de pacote |

### 2.2 `BD-Novo` — cadastros

**Tabela `Filiais`** (`Local de negócios`, `Filial`): 16 linhas — 0010 ARAUCARIA, 0009 BELEM, 0014 CAMPO GRANDE, 0002 CRUZEIRO DO SUL, 0008 ITAITUBA, 0001 MANAUS, 0018 PAULINIA, 0016 PORTO NACIONAL, 0003 PORTO VELHO, 0011 SANTAREM, 0017 SAO LUIS, 0026 SAO PAULO, 0019 SENADOR CANEDO, 0012 SINOP, 0005 VARZEA GRANDE, 0004 VILHENA.

**Tabela `Centro_de_Custos`** (`Centro de Custo`, `Denominação`; no CAPEX também `Gestor do Centro de Custo`).

**Tabela `Pacotes`** (`Descrição`, `Conta do Razão`, `Agrupamento DRE`, `Pacote GMD`, `Detalhamento`) — 63 contas OPEX:

| Pacote GMD | Contas (código – descrição) |
|---|---|
| Comercial | 6030101013 Descontos Concedidos (Res. Financeiro); 6010501005 Comissão s/ Vendas; 6010501006 Manutenção em Postos de Terceiros; 6010501009 Serviços de Despachantes |
| Comunicação e marketing | 6010501001 Propaganda e Publicidade; 6010501002 Comemorações, Promoções e Eventos; 6010501003 Patrocínios; 6010301029 Festas e Confraternizações; 6020201001 Doações, Brindes e Cortesias |
| Consumo e Expediente | 6010301016 Uniformes; 6010301017 Equip. de Segurança; 6010301008 Alimentação; 6010301006 Material de Consumo; 6010301005 Material de Escritório; 6010301003 Energia Elétrica; 6010301004 Água e Saneamento; 6010301022 Bens de Pequeno Valor; 6010301023 Despesas Postais; 6010301007 Associação de Classes |
| DTI | 6010301034 Aluguéis de Software e Hardware (*Locação Impressoras e Máquinas*); 6010201011 Serv. Informática, Licença e Software (*Renovação de Licença*); 6010301002 Telefonia, Links e Internet (*Links de Internet*) |
| Infraestrutura e Instalações | 6010301019, 6010301031, 6010301018, 6020101008, 6010301026, 6010201005, 6020101002, 6010201007, 6010201010, 6010201008 |
| Jurídico | 6010301015 Publicações; 6010201004 Honorários Advocatícios; 6010301009 Custas e Acordos Judiciais; 6010301024 Custas Cartoriais |
| Logística e Transporte | 6010301032, 6010301030, 6020101001, 6010301020, 6010301025, 6010301021, 6010301013, 6010201006, 6010301010, 6010201012 |
| Segurança e Seguros Patrimoniais | 6010301012 Seguro Predial; 6010201009 Vigilância; 6010301027 Seguros diversos |
| Serviços de Terceiros | 6010201002 PF; 6010201003 Consultoria; 6010501013 PJ e PF; 6010201014 Auditoria; 6010201001 PJ |
| Tesouraria | 6030101012 Tarifa s/ Cobrança; 6030101001 Despesas Bancárias (Res. Financeiro) |
| Tributário | 6020101004 INMETRO; 6020101006 IBAMA; 6020101007 SUFRAMA; 6020101010 Taxas Diversas |
| Viagens | 6010301001 Hospedagem; 6010301011 Passagens; 6010301036 Diária de Viagem |

Coluna **Detalhamento** só é usada pelo DTI: o usuário escolhe o *produto/serviço* e a conta é derivada (`XLOOKUP` com `FILTER` por pacote). → no sistema: entidade `account_details` (detalhamento → conta).

**Listas de domínio (BD-Novo):**

| Lista | Valores | Uso |
|---|---|---|
| Cargos (viagem) | Anal./Espec./Coord., Gerentes, Diretores | Viagens |
| Multiplicadores | 1 / 1,5 / 2 por cargo | (não referenciado por fórmulas — legado) |
| Tipo de viagem | Nacional, Internacional, Interior AM | Viagens |
| Meses | JAN…DEZ | Viagens, Eventos |
| Evento / Valor alimentação | Interno = R$ 60; Externo = R$ 120 (por pessoa) | Comunicação e MKT |
| Tipo de obra | Preventiva, Corretiva, Melhoria | Infraestrutura |
| Prioridade | 1-Urgente, 2-Normal, 3-Baixa | Infraestrutura |
| Obrigatoriedade | SIM, NÃO | Infraestrutura |
| Probabilidade (processo) | PROVÁVEL, POSSÍVEL, REMOTO | Jurídico (CPC 25) |
| Tipo de contrato DTI | Manutenção de Sistema, Melhoria de Sistema, Segurança da informação, Novo sistema | DTI |
| Aluguel de veículos (diária) | Nacional: 80/100/150; Interior AM: 200/250/280 por cargo | Logística |
| Listas por pacote | `SORT(UNIQUE(FILTER(Pacotes[Descrição], Pacote GMD = X)))` | Validação da conta em cada aba |

### 2.3 `Realizado 2026`

Tabela `Realizado_2026` em formato **largo**: `Empresa, Filial, Nome Filial, Centro de Custos, Denominação CC, Gestor do CC, Conta Razão, Denominação Conta, Pacotes Orçamento, 01/01/2026 … 01/08/2026, TOTAL REALIZADO 2026`. No template recebido vem vazia ("Não há realizado no centro de custo") — dado é injetado pela Controladoria por gestor. **No sistema vira fato `actual_entries` em formato longo (uma linha por competência)**, carregado por importação.

### 2.4 `Resumo 2027`

- `Realizado até Ago/26` = `SUMIFS(Realizado_2026[TOTAL], [Pacotes Orçamento], pacote)`.
- JAN…DEZ = linha "Consolidador"/"Subtotal" de cada aba de pacote; `TOTAL 2027 = SUM(JAN:DEZ)`; `TOTAL GERAL`.
- → No sistema: **consulta agregada** (view) por pacote × mês, sem armazenar totais.

### 2.5 Abas de pacote — estrutura comum

Toda linha de pacote tem: `CHAVE`, `FILIAL`→`DIVISÃO` (código via `XLOOKUP` em Filiais), `DENOMINAÇÃO CC`→`CENTRO DE CUSTO` (XLOOKUP), `DESCRIÇÃO DA CONTA`→`CONTA CONTÁBIL` (XLOOKUP em Pacotes, restrito ao pacote por validação), 12 meses (decimal ≥ 0 em várias abas) e `2027 = SUM(meses)`. Totais por `SUBTOTAL(9, …)`.

`CHAVE = "1001-" & divisão & "-" & cc & "-" & conta` — **empresa 1001 fixa** (template só serve à ATEM). No sistema a empresa vem do CC.

Campos específicos por pacote (entrada do gestor):

| Aba | Campos descritivos | Valor | Regra de cálculo |
|---|---|---|---|
| I – Viagens | Objetivo, Cargo, Ida (mês), Volta (mês), Período (dias), Origem (UF), Destino, Tipo | **Calculado** | ver 2.6 |
| II – Serv. Terceiros | Detalhamento do contrato, Gestor do contrato, Fornecedor, Observações (reajuste/novo/descontinuado) | 12 meses digitados | soma |
| III – Comunic. e MKT | Detalhamento, Interno/Externo, Objetivo do evento, Qtd pessoas, Mês do evento, Local, Material gráfico, Estrutura, Brindes, Deslocamento | **Calculado** | ver 2.7 |
| IV – Infra e Instal. | Detalhamento, Justificativa, Tipo de obra, Obrigatoriedade, Material necessário, Serviço necessário, Prioridade | 12 meses | soma |
| V – Jurídico | Detalhamento, Natureza do processo, Escritório, Probabilidade de sucesso, Objeto do contrato, Valor inicial da causa, Valor final/contrato | 12 meses | soma |
| VI – DTI | Detalhamento, Vigência, Tipo de contrato, **Produto/Serviço** (→ conta) | 12 meses | conta derivada do detalhamento |
| VII – Consumo e Exp. | Detalhamento, Fornecedor, Observações | 12 meses | soma |
| VIII – Segurança e Seguros | Detalhamento/contrato, Gestor do contrato, Fornecedor, Observações | 12 meses | soma |
| IX – Logística | Detalhamento | 12 meses | soma (diária de locação = nº dias × diária por cargo — premissa manual) |
| X – Tributário / XI – Tesouraria | Detalhamento, Finalidade | 12 meses | soma |
| XII – Comercial | Detalhamento | 12 meses | soma |

### 2.6 Regra de cálculo — Viagens

Parâmetros na própria aba:
- **Matriz de passagens** `Destino (linhas F8:F46) × Origem UF (colunas G7:AG7)` — 27 UFs + Interior AM + 11 destinos internacionais. *Vazia no template* ("este primeiro template não contempla os valores das passagens").
- **Hospedagem/dia** por Tipo × Cargo: Nacional 600/900/1.200; Internacional 1.000/1.400/2.000; Interior AM 300/450/600.
- **Diária de viagem/dia**: Nacional 150; Internacional 200; Interior AM 150 (todos os cargos).
- **Aluguel de veículo/dia** (referência para Logística): Nacional 80/100/150; Internacional 0; Interior AM 200/250/280.

Fórmulas:
```
Passagem  = matriz[destino, origem]            se VOLTA preenchida (ida e volta)
          = matriz[destino, origem] / 2        se só IDA
Diária    = tarifa_diaria[tipo, cargo] × dias
Hospedagem= tarifa_hosp[tipo, cargo]  × dias
Lançamento: tudo no mês de IDA, em 3 contas (6010301011, 6010301036, 6010301001)
```
O "Consolidador" (colunas AB:AO) faz `UNIQUE` das chaves e `SUMIFS` por mês de ida → 3 linhas orçamentárias por viagem.

### 2.7 Regra de cálculo — Eventos (Comunicação e MKT)

```
Alimentação = valor_por_pessoa[Interno=60 | Externo=120] × qtd_pessoas
Total       = Alimentação + Material gráfico + Estrutura + Brindes + Deslocamento
Mês         = Total lançado integralmente no MÊS DO EVENTO
```

---

## 3. Template CAPEX

### 3.1 Abas

| Aba | Estado | Função |
|---|---|---|
| `Instruções` | visível | Requisitos CAPEX, prazo 15/10/2026, responsáveis, exemplos |
| `Template_Orç 2027` | visível | Linhas de solicitação (7:100) |
| `Tipos de Projetos` | oculta | 8 tipos |
| `BD-Novo` | oculta | Filiais, CC (com gestor), Pacotes = contas de ativo |
| `LISTA ATIVOS` | oculta | Item principal → classe de ativo → conta, por empresa (ATEM/REAM) |

### 3.2 Colunas de `Template_Orç 2027`

| Coluna | Tipo | Regra |
|---|---|---|
| NOME FILIAL / FILIAL | entrada / calculado | XLOOKUP Filiais |
| NOME CC / CC | entrada / calculado | XLOOKUP CC |
| DESCRIÇÃO DA CONTA / CONTA | entrada / calculado | XLOOKUP Pacotes (contas de ativo) |
| Projeto? | lista `Sim,Não` | se Sim → Tipo de projeto e justificativa quantificada obrigatórios |
| TIPO DO PROJETO | lista | Automação e Transformação Digital; Embandeiramento; Expansão de Infraestrutura Operacional ou ADM; Implantação de Novos Negócios; Manutenção; Modernização e Eficiência Operacional; Readequação Operacional e Administrativa; Segurança |
| ITEM, DESCRIÇÃO DETALHADA | texto | |
| VLR UNIT, QTD | número | |
| VLR TOTAL | calculado | `= VLR UNIT × QTD` |
| JUSTIFICATIVA | texto | projetos: objetivo + redução de custo / geração de receita estimada |
| 12 meses (cronograma) | número | competência da execução |
| Orçamento (soma meses) | calculado | `SUM(meses)` |
| Check | calculado | `IF(soma_meses − total = 0, "ok", "diferença - verificar")` |
| Chave orçamentária | calculado | `1001-FILIAL-CC-CONTA` |

**Contas CAPEX (BD-Novo):** 1010901001 Bonificações Pagas; 1020701006 Luvas de Contratos de Locação; 1020701004 Intangível em Andamento; 1020701002 Licenças e Software; 1020601015 Aeronave; 1020601014 Juros Capitalizados; 1020601010 Adiantamento p/ Imobilizações; 1020601008 Benfeitorias em Imóveis de Terceiros; 1020601007 Veículos; 1020601006 Embarcações; 1020601005 Equip. de Informática; 1020601004 Móveis, Utensílios e Instalações; 1020601003 Equip. e Máquinas; 1020601002 Edifícios e Construções; 1020601001 Terrenos; 1020502002 Investimentos em Controladas; 6090201001 Imobilizado em Andamento; 6090201002 Intangível em Andamento. Agrupamento DRE = "Sem Agrupamento", Pacote = "Capex".

**LISTA ATIVOS:** ~170 itens (ABRACADEIRA, ACM, ADAPTADOR, AMPLIAÇÃO BASE, AQUISIÇÃO BALSA…) mapeados a 13 classes de ativo e respectivas contas, com contagem de ativos por empresa (Qtd ATEM / Qtd REAM). → No sistema: catálogo `asset_items` (item → classe → conta) para **sugerir a conta** a partir do item.

**Requisitos de enquadramento (validação):** valor unitário > R$ 1.200; vida útil > 12 meses; aquisição/criação/melhoria de ativo.

---

## 4. Template Pessoal

### 4.1 `QUADRO FUNCIONARIOS`

Cabeçalho-resumo:
```
Custo Real   (F8) = SUM(H16:H27)          ← soma salários (faixa fixa!)
Premissa     (E9) = 5%                    ← reajuste
Total c/ prem.(F9) = F8 × (1 + E9)
Crescimento  (F10)= F9 − F8
```

Colunas por colaborador:

| Bloco | Colunas |
|---|---|
| Dados do funcionário | MATRÍCULA, NOME, CARGO ATUAL, EMPRESA, DIVISÃO, CENTRO DE CUSTO, SALÁRIO MENSAL |
| Dados da ação | AÇÃO (`MANTER`, `PROMOVER`, `INCLUIR`, `REMOVER`), MÊS DA AÇÃO (1–12), NOVO CARGO, NOVO SALÁRIO |
| Projeção mensal | JAN…DEZ (salário projetado do mês) |
| Dados do líder (benefícios) | Auxílio creche (6010103005), Total dependentes (6010103003), [oculta] (6010101009), Vale transporte (6010103001), Auxílio faculdade (6010103010), Adicional noturno (6010101008), Adicional 30% (6010101002), Auxílio combustível (6010103006, decimal ≥ 80), Auxílio farmácia (conta "????"), Abono ACT, Estacionamento — SIM/NÃO |

**Regra de projeção mensal** (linha 48+):
```
mes(m) =  H                       se AÇÃO = MANTER
          L  se m ≥ J  senão H    se AÇÃO = PROMOVER   (novo salário a partir do mês)
          0  se m ≥ J  senão H    se AÇÃO = REMOVER    (desligamento)
          L  se m ≥ J  senão H(=0) se AÇÃO = INCLUIR   (contratação)
```
Os benefícios carregam a **conta contábil** na linha 13 → cada benefício é uma conta de pessoal (família 60101xxxxx).

### 4.2 `PREMISSAS MACROECONOMICAS` (oculta)

Indicadores × fonte (BACEN/Focus, Bradesco, Itaú, Santander, Bloomberg, Platts) × ano (2022–2026) com data de atualização: IPCA, PIB Brasil, PIB Norte, CDI, PTAX, Dated Brent. Premissas de negócio: crescimento de receita, volumes por produto (Diesel, Gasolina, Hidratado, GNV) e por segmento (ATEM, COF, COF-CONTR, COFG, COG, ENERGIA, POSBB, TAG, TRR), preço médio de venda/compra, cobertura de estoque, margem, CBIOs, PDD, crescimento de despesas. Marcada "*Pendente atualizar" (datas de 2023).

→ No sistema: tabela `macro_assumptions(indicator, segment, source, year, value, unit, updated_at)` versionada por importação; uma fonte marcada como **oficial** por indicador/ano.

---

## 5. Problemas e fragilidades encontradas (justificam o redesenho)

| # | Template | Problema | Tratamento no sistema |
|---|---|---|---|
| 1 | Todos | Empresa `1001` fixa na chave | Empresa derivada do CC |
| 2 | Todos | Tabela de CC só tem o CC do gestor; cada arquivo é uma cópia | Cadastro único + escopo por usuário |
| 3 | OPEX | Gestores/tipo de pacote divergentes entre Excel e cartilha | `budget_packages` + `package_managers` parametrizados por ciclo |
| 4 | OPEX | Matriz de passagens vazia | Tabela `travel_fares` importável; linha fica com alerta "tarifa não cadastrada" |
| 5 | OPEX | Passagem só-ida = ida-e-volta / 2 | Mantido como regra, parametrizável (`one_way_factor = 0,5`) |
| 6 | OPEX | Lista `Multiplicadores` não usada por fórmula | Ignorada (documentado) |
| 7 | OPEX | `Lista_Seguranca_Seguros` e `Lista_Operacoes` apontam para a mesma coluna | Lista derivada da conta → pacote |
| 8 | CAPEX | Cabeçalhos mensais datados **2026** e coluna "Orçamento 2026" em template 2027 | Meses gerados pelo `fiscal_year` do ciclo |
| 9 | CAPEX | Check textual não impede envio | Inconsistência **crítica** bloqueia aprovação |
| 10 | CAPEX | `LISTA ATIVOS` com `#REF!` e nomes quebrados | Catálogo limpo importável |
| 11 | Pessoal | Rótulo "2023" sobre os meses; fórmulas só a partir da linha 48 (linhas 16–19 sem projeção) | Cálculo no backend para todos |
| 12 | Pessoal | `Custo Real = SUM(H16:H27)` faixa fixa | Agregação dinâmica |
| 13 | Pessoal | Custo = só salário; sem encargos/benefícios; PJ tratado igual a CLT | Multiplicador por tipo de contrato (CLT 1,8; PJ sem multiplicador) |
| 14 | Pessoal | Conta do auxílio farmácia = "????" | Benefício sem conta gera alerta de cadastro |
| 15 | Pessoal | Premissas macro de 2023 | Importação versionada + data de atualização visível |
| 16 | Todos | Sem trilha de auditoria, versão ou workflow; envio por e-mail | Workflow + auditoria + versões |
