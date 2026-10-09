"""Dados de referência extraídos dos templates 2027 e da Cartilha (ver docs/01-analise-templates.md).

São valores iniciais: todos editáveis no sistema (cadastros/parâmetros) e substituíveis por importação.
"""

# Estrutura da Controladoria (quadro "Por área", 07/10/2026): Área → Setor de cada centro de custo da ATEM (1001).
# No modelo, a Área fica em `departments` e o Setor em `areas`. O seed só preenche CCs ainda sem área e setor;
# depois disso vale o que estiver em Cadastros (Projeção e novos setores entram por lá).
CC_STRUCTURE = {
    "1050101000": ("Controladoria", "Diretoria"),
    "1050101001": ("Controladoria", "Controladoria"),
    "1050101002": ("Controladoria", "Contabilidade"),
    "1050101004": ("Controladoria", "Custos"),
    "1050101006": ("Controladoria", "Auditoria Externa"),
    "1050101003": ("Tributos", "Fiscal"),
    "1050101007": ("Tributos", "Planejamento Tributário"),
    "1050101008": ("Tributos", "Comex"),
}
# demais CCs da faixa da Controladoria (1050101…), pelo nome
CC_STRUCTURE_PREFIX = "1050101"
CC_STRUCTURE_BY_NAME = (("CSC", ("Controladoria", "CSC")), ("DADOS", ("Controladoria", "Dados")))

ROLES = {
    "ADMIN": "Administrador",
    "CONTROLLER": "Validador / Controladoria",
    "MANAGER": "Gestor de Centro de Custo",
    "PACKAGE_MANAGER": "Gestor de Pacote (GMD)",
    "HR": "Recursos Humanos",
    "VIEWER": "Consulta",
}

COMPANIES = [("1001", "ATEM", "ATEM"), ("2001", "REAM", "REAM"), ("1012", "NAVE", "NAVE")]

# Filiais / locais de negócio (BD-Novo → tabela Filiais), empresa 1001
BRANCHES = [
    ("0010", "ARAUCARIA", "PR"),
    ("0009", "BELEM", "PA"),
    ("0014", "CAMPO GRANDE", "MS"),
    ("0002", "CRUZEIRO DO SUL", "AC"),
    ("0008", "ITAITUBA", "PA"),
    ("0001", "MANAUS", "AM"),
    ("0018", "PAULINIA", "SP"),
    ("0016", "PORTO NACIONAL", "TO"),
    ("0003", "PORTO VELHO", "RO"),
    ("0011", "SANTAREM", "PA"),
    ("0017", "SAO LUIS", "MA"),
    ("0026", "SAO PAULO", "SP"),
    ("0019", "SENADOR CANEDO", "GO"),
    ("0012", "SINOP", "MT"),
    ("0005", "VARZEA GRANDE", "MT"),
    ("0004", "VILHENA", "RO"),
]

# code, nome, romano, tipo (1 = validação obrigatória, 2 = consultivo), natureza, formulário, ordem
PACKAGES = [
    ("VIAGENS", "Viagens", "I", 1, "OPEX", "TRAVEL", 1),
    ("SERVICOS_TERCEIROS", "Serviços de Terceiros", "II", 1, "OPEX", "GENERIC", 2),
    ("COMUNICACAO_MARKETING", "Comunicação e marketing", "III", 1, "OPEX", "EVENT", 3),
    ("INFRAESTRUTURA", "Infraestrutura e Instalações", "IV", 1, "OPEX", "GENERIC", 4),
    ("JURIDICO", "Jurídico", "V", 2, "OPEX", "GENERIC", 5),
    ("DTI", "DTI", "VI", 2, "OPEX", "GENERIC", 6),
    ("CONSUMO_EXPEDIENTE", "Consumo e Expediente", "VII", 2, "OPEX", "GENERIC", 7),
    ("SEGURANCA_SEGUROS", "Segurança e Seguros Patrimoniais", "VIII", 2, "OPEX", "GENERIC", 8),
    ("LOGISTICA_TRANSPORTE", "Logística e Transporte", "IX", 2, "OPEX", "GENERIC", 9),
    ("TRIBUTARIO", "Tributário", "X", 2, "OPEX", "GENERIC", 10),
    ("TESOURARIA", "Tesouraria", "XI", 2, "FINANCEIRO", "GENERIC", 11),
    ("COMERCIAL", "Comercial", "XII", 2, "OPEX", "GENERIC", 12),
    ("OPERACOES_FINANCEIRAS", "Operações Financeiras", None, 2, "FINANCEIRO", "GENERIC", 13),
    ("PESSOAS", "Pessoas", None, 1, "PESSOAL", "PERSONNEL", 14),
    ("CAPEX", "Capex", None, 2, "CAPEX", "CAPEX", 15),
]

# conta, descrição, agrupamento DRE, pacote (nome), detalhamento
ACCOUNTS = [
    ("6030101013", "Descontos Concedidos", "Resultado Financeiro", "Comercial", None),
    ("6010501005", "Comissão s/ Vendas", "Despesas", "Comercial", None),
    ("6010501006", "Manutenção em Postos de Terceiros", "Despesas", "Comercial", None),
    ("6010501009", "Serviços de Despachantes", "Despesas", "Comercial", None),
    ("6010501001", "Propaganda e Publicidade", "Despesas", "Comunicação e marketing", None),
    ("6010501002", "Comemorações, Promoções e Eventos", "Despesas", "Comunicação e marketing", None),
    ("6010501003", "Patrocínios", "Despesas", "Comunicação e marketing", None),
    ("6010301029", "Festas e Confraternizações", "Despesas", "Comunicação e marketing", None),
    ("6020201001", "Doações, Brindes e Cortesias", "Despesas", "Comunicação e marketing", None),
    ("6010301016", "Uniformes", "Despesas", "Consumo e Expediente", None),
    ("6010301017", "Equipamentos de Segurança", "Despesas", "Consumo e Expediente", None),
    ("6010301008", "Alimentação", "Despesas", "Consumo e Expediente", None),
    ("6010301006", "Material de Consumo", "Despesas", "Consumo e Expediente", None),
    ("6010301005", "Material de Escritório", "Despesas", "Consumo e Expediente", None),
    ("6010301003", "Energia Elétrica", "Despesas", "Consumo e Expediente", None),
    ("6010301004", "Água e Saneamento Básico", "Despesas", "Consumo e Expediente", None),
    ("6010301022", "Bens de Pequeno Valor", "Despesas", "Consumo e Expediente", None),
    ("6010301023", "Despesas Postais", "Despesas", "Consumo e Expediente", None),
    ("6010301007", "Associação de Classes", "Despesas", "Consumo e Expediente", None),
    ("6010301034", "Alugueis de Software e Hardware", "Despesas", "DTI", "Locação Impressoras e Maquinas"),
    ("6010201011", "Serviços Informática, Licença e Software", "Despesas", "DTI", "Renovação de Licença"),
    ("6010301002", "Telefonia, Links e Internet", "Despesas", "DTI", "Links de Internet"),
    ("6010301019", "Aluguéis de Máquinas e Equipamentos", "Despesas", "Infraestrutura e Instalações", None),
    ("6010301031", "Manutenção de Imóveis (Material)", "Despesas", "Infraestrutura e Instalações", None),
    ("6010301018", "Aluguéis/Condomínios", "Despesas", "Infraestrutura e Instalações", None),
    ("6020101008", "Alvará", "Despesas", "Infraestrutura e Instalações", None),
    ("6010301026", "Manutenção de máquinas e equipamentos (Serviço)", "Despesas", "Infraestrutura e Instalações", None),
    ("6010201005", "Manutenção de Imóveis (Serviço)", "Despesas", "Infraestrutura e Instalações", None),
    ("6020101002", "IPTU", "Despesas", "Infraestrutura e Instalações", None),
    (
        "6010201007",
        "Manutenção Equipto/Máquinas/Instalações (Material)",
        "Despesas",
        "Infraestrutura e Instalações",
        None,
    ),
    ("6010201010", "Serviços de Análises Laboratoriais", "Despesas", "Infraestrutura e Instalações", None),
    ("6010201008", "Conservação e Limpeza", "Despesas", "Infraestrutura e Instalações", None),
    ("6010301015", "Publicações", "Despesas", "Jurídico", None),
    ("6010201004", "Honorários Advocatícios", "Despesas", "Jurídico", None),
    ("6010301009", "Custas e Acordos Judiciais", "Despesas", "Jurídico", None),
    ("6010301024", "Custas Cartoriais", "Despesas", "Jurídico", None),
    ("6010301032", "Manutenção de Veículos (Material)", "Despesas", "Logística e Transporte", None),
    ("6010301030", "Manutenção de Aeronave", "Despesas", "Logística e Transporte", None),
    ("6020101001", "IPVA e Licenciamento", "Despesas", "Logística e Transporte", None),
    ("6010301020", "Aluguéis de Embarcações", "Despesas", "Logística e Transporte", None),
    ("6010301025", "Conduções e Táxis", "Despesas", "Logística e Transporte", None),
    ("6010301021", "Aluguéis de Veículos", "Despesas", "Logística e Transporte", None),
    ("6010301013", "Seguro de Veículos", "Despesas", "Logística e Transporte", None),
    ("6010201006", "Manutenção de Veículos (Serviço)", "Despesas", "Logística e Transporte", None),
    ("6010301010", "Combustíveis e Lubrificantes", "Despesas", "Logística e Transporte", None),
    ("6010201012", "Serviços de Fretes e Carretos", "Despesas", "Logística e Transporte", None),
    ("6010301012", "Seguro Predial", "Despesas", "Segurança e Seguros Patrimoniais", None),
    ("6010201009", "Serviços de Vigilância", "Despesas", "Segurança e Seguros Patrimoniais", None),
    ("6010301027", "Seguro diversos", "Despesas", "Segurança e Seguros Patrimoniais", None),
    ("6010201002", "Serviços de Pessoa Física", "Despesas", "Serviços de Terceiros", None),
    ("6010201003", "Serviços de Consultoria e Assessoria", "Despesas", "Serviços de Terceiros", None),
    ("6010501013", "Serviços Prestados de PJ e PF", "Despesas", "Serviços de Terceiros", None),
    ("6010201014", "Serviços de Auditoria", "Despesas", "Serviços de Terceiros", None),
    ("6010201001", "Serviços de Pessoa Jurídica", "Despesas", "Serviços de Terceiros", None),
    ("6030101012", "Tarifa s/ Cobrança", "Resultado Financeiro", "Tesouraria", None),
    ("6030101001", "Despesas Bancárias", "Resultado Financeiro", "Tesouraria", None),
    ("6020101004", "INMETRO", "Despesas", "Tributário", None),
    ("6020101006", "IBAMA", "Despesas", "Tributário", None),
    ("6020101007", "SUFRAMA", "Despesas", "Tributário", None),
    ("6020101010", "Taxas Diversas", "Despesas", "Tributário", None),
    ("6010301001", "Hospedagem", "Despesas", "Viagens", None),
    ("6010301011", "Despesas com Passagens", "Despesas", "Viagens", None),
    ("6010301036", "Diária de viagem", "Despesas", "Viagens", None),
    # CAPEX (template CAPEX → BD-Novo)
    ("1010901001", "Bonificações Pagas", "Sem Agrupamento", "Capex", None),
    ("1020701006", "Luvas de Contratos de Locação", "Sem Agrupamento", "Capex", None),
    ("1020701004", "Intangível em Andamento", "Sem Agrupamento", "Capex", None),
    ("1020701002", "Licenças e Software", "Sem Agrupamento", "Capex", None),
    ("1020601015", "Aeronave", "Sem Agrupamento", "Capex", None),
    ("1020601014", "Juros Capitalizados", "Sem Agrupamento", "Capex", None),
    ("1020601010", "Adiantamento para Imobilizações", "Sem Agrupamento", "Capex", None),
    ("1020601008", "Benfeitorias em Imóveis de Terceiros", "Sem Agrupamento", "Capex", None),
    ("1020601007", "Veículos", "Sem Agrupamento", "Capex", None),
    ("1020601006", "Embarcações", "Sem Agrupamento", "Capex", None),
    ("1020601005", "Equipamentos de Informática", "Sem Agrupamento", "Capex", None),
    ("1020601004", "Móveis, Utensílios e Instalações", "Sem Agrupamento", "Capex", None),
    ("1020601003", "Equipamentos e Máquinas", "Sem Agrupamento", "Capex", None),
    ("1020601002", "Edifícios e Construções", "Sem Agrupamento", "Capex", None),
    ("1020601001", "Terrenos", "Sem Agrupamento", "Capex", None),
    ("1020601009", "Imobilizado em Andamento (Ativo)", "Sem Agrupamento", "Capex", None),
    ("1020502002", "Investimentos em Controladas", "Sem Agrupamento", "Capex", None),
    ("1020501001", "Imóveis para Investimentos", "Sem Agrupamento", "Capex", None),
    ("6090201001", "Imobilizado em Andamento", "Sem Agrupamento", "Capex", None),
    ("6090201002", "Intangível em Andamento (Resultado)", "Sem Agrupamento", "Capex", None),
    # Pessoal (cabeçalho de benefícios do template Pessoal, linha 13)
    ("6010103005", "Auxílio Creche", "Despesas", "Pessoas", None),
    ("6010103003", "Dependentes", "Despesas", "Pessoas", None),
    ("6010103001", "Vale Transporte", "Despesas", "Pessoas", None),
    ("6010103010", "Auxílio Faculdade", "Despesas", "Pessoas", None),
    ("6010101008", "Adicional Noturno", "Despesas", "Pessoas", None),
    ("6010101002", "Adicional 30%", "Despesas", "Pessoas", None),
    ("6010103006", "Auxílio Combustível", "Despesas", "Pessoas", None),
]

# Classes de ativo (LISTA ATIVOS) → conta
ASSET_CLASSES = [
    ("Terrenos", "1020601001"),
    ("Edifícios e Construções", "1020601002"),
    ("Equipamentos e Máquinas", "1020601003"),
    ("Móveis, Utensílios e Instalações", "1020601004"),
    ("Equipamentos de Informática", "1020601005"),
    ("Embarcações", "1020601006"),
    ("Veículos", "1020601007"),
    ("Benfeitorias em Imóveis de Terceiros", "1020601008"),
    ("Imobilizado em Andamento", "1020601009"),
    ("Licenças e Software", "1020701002"),
    ("Luvas de Contratos de Locação", "1020701006"),
    ("Bonificações", "1010901001"),
    ("Imóveis para Investimentos", "1020501001"),
]

UFS = [
    "AC",
    "AL",
    "AP",
    "AM",
    "BA",
    "CE",
    "DF",
    "ES",
    "GO",
    "MA",
    "MT",
    "MS",
    "MG",
    "PA",
    "PB",
    "PR",
    "PE",
    "PI",
    "RJ",
    "RN",
    "RS",
    "RO",
    "RR",
    "SC",
    "SP",
    "SE",
    "TO",
]
INTERNATIONAL = [
    "RUSSIA",
    "EUA",
    "GUIANA FRANCESA",
    "COLÔMBIA",
    "VENEZUELA",
    "INGLATERRA (LONDRES)",
    "LITUANIA",
    "SINGAPURA",
    "EMIRADOS ÁRABES UNIDOS (DUBAI)",
    "PANAMÁ",
    "PERU (LIMA)",
]

JOB_LEVELS = ["Anal./Espec./Coord.", "Gerentes", "Diretores"]


def _lookup(domain: str, values: list, extra: dict | None = None) -> list[tuple]:
    rows = []
    for i, v in enumerate(values, start=1):
        code, label = (v if isinstance(v, tuple) else (v, v))[:2]
        rows.append((domain, code, label, i, (extra or {}).get(code)))
    return rows


LOOKUPS = (
    _lookup("TRIP_TYPE", ["Nacional", "Internacional", "Interior AM"])
    + _lookup("JOB_LEVEL", JOB_LEVELS)
    + _lookup(
        "EVENT_TYPE", ["Interno", "Externo"], {"Interno": {"meal_per_person": 60}, "Externo": {"meal_per_person": 120}}
    )
    + _lookup("MEAL_TYPE", ["Café da manhã", "Almoço/Jantar", "Lanche"])
    + _lookup("WORK_TYPE", ["Preventiva", "Corretiva", "Melhoria"])
    + _lookup("PRIORITY", ["1-Urgente", "2-Normal", "3-Baixa"])
    + _lookup("YES_NO", ["SIM", "NÃO"])
    + _lookup("LAWSUIT_PROBABILITY", ["PROVÁVEL", "POSSÍVEL", "REMOTO"])
    + _lookup(
        "DTI_CONTRACT_TYPE", ["Manutenção de Sistema", "Melhoria de Sistema", "Segurança da informação", "Novo sistema"]
    )
    + _lookup(
        "CAPEX_PROJECT_TYPE",
        [
            "Automação e Transformação Digital",
            "Embandeiramento",
            "Expansão de Infraestrutura Operacional ou ADM",
            "Implantação de Novos Negócios",
            "Manutenção",
            "Modernização e Eficiência Operacional",
            "Readequação Operacional e Administrativa",
            "Segurança",
        ],
    )
    + _lookup(
        "PERSONNEL_ACTION",
        [
            ("KEEP", "Manter"),
            ("PROMOTION", "Promover"),
            ("HIRE", "Incluir"),
            ("TERMINATION", "Remover"),
            ("SALARY_ADJUSTMENT", "Reajuste"),
            ("TRANSFER", "Transferência"),
        ],
    )
    + _lookup("TRAVEL_ORIGIN", UFS + ["Interior AM"])
    + _lookup(
        "TRAVEL_DESTINATION",
        UFS + ["Interior AM"] + INTERNATIONAL,
        {
            **{u: {"trip_type": "Nacional"} for u in UFS},
            "Interior AM": {"trip_type": "Interior AM"},
            **{i: {"trip_type": "Internacional"} for i in INTERNATIONAL},
        },
    )
)

# rate_type, trip_type, cargo, valor diário (aba I - Viagens, colunas AJ:AL)
TRAVEL_RATES = [
    *[
        ("LODGING", t, j, v)
        for t, vals in (
            ("Nacional", (600, 900, 1200)),
            ("Internacional", (1000, 1400, 2000)),
            ("Interior AM", (300, 450, 600)),
        )
        for j, v in zip(JOB_LEVELS, vals, strict=True)
    ],
    *[
        ("PER_DIEM", t, j, v)
        for t, v in (("Nacional", 150), ("Internacional", 200), ("Interior AM", 150))
        for j in JOB_LEVELS
    ],
    *[
        ("CAR_RENTAL", t, j, v)
        for t, vals in (("Nacional", (80, 100, 150)), ("Internacional", (0, 0, 0)), ("Interior AM", (200, 250, 280)))
        for j, v in zip(JOB_LEVELS, vals, strict=True)
    ],
]

CYCLE_PARAMETERS = {
    "review.justification_blocks": (False, "Justificativa faltando bloqueia o envio (OPEX, Pessoal e CAPEX)"),
    "alert.growth_pct": (0.20, "Alerta: crescimento 2027 vs realizado 2026 anualizado acima de X"),
    "alert.reduction_pct": (0.30, "Alerta: redução 2027 vs realizado 2026 anualizado acima de X"),
    "alert.history_band_pct": (0.25, "Banda aceitável em torno da média histórica (2025 e 2026)"),
    "alert.min_relevant_amount": (1000, "Valor mínimo para alertar conta sem orçamento"),
    "travel.one_way_factor": (0.5, "Passagem só de ida = tarifa ida/volta × fator"),
    "travel.route_estimates": (
        {
            "AM>SP": 3200,
            "SP>AM": 3200,
            "CE>AM": 2900,
            "AM>PA": 2200,
            "AM>RJ": 3500,
            "AM>RO": 2200,
            "AM>AP": 2500,
            "AM>GO": 3200,
            "AM>MT": 3500,
            "AM>PR": 3800,
            "AM>SC": 4000,
            "CE>SP": 2200,
            "AM>PERU (LIMA)": 5500,
        },
        "Passagem estimada por rota (ida e volta, R$) — chave ORIGEM>DESTINO",
    ),
    "travel.flat_estimate": (4000, "Passagem estimada: valor fixo por viagem (R$) quando não houver rota"),
    "capex.min_unit_value": (1200, "Valor unitário mínimo para enquadramento como CAPEX"),
    "capex.min_useful_life_months": (12, "Vida útil mínima (meses) para CAPEX"),
    "personnel.salary_adjustment_pct": (0.05, "Premissa de reajuste salarial (template Pessoal, E9)"),
    "personnel.adjustment_month": (1, "Mês de aplicação do reajuste (data-base)"),
    # Consolidação: contas em que o custo de pessoal é lançado (confirmar com a contabilidade)
    "personnel.salary_account": (6010101001, "Conta do salário (com reajuste) na consolidação e na carga SAP"),
    "personnel.charges_account": (6010102001, "Conta de encargos e benefícios (parte do multiplicador)"),
    "personnel.severance_account": (6010101010, "Conta das verbas rescisórias"),
    # abono anual do CLT (09/10/2026): ~R$ 2.500 por colaborador no ano, em 12 parcelas, sem multiplicador
    "personnel.annual_bonus_clt": (2500, "Abono anual por colaborador CLT (R$/ano, diluído em 12 parcelas)"),
    "personnel.annual_bonus_account": (6010101009, "Conta do abono anual do CLT (Gratificações/Premiações)"),
    "personnel.charges_split": (
        {
            # mix do realizado 2026 (KSB1, empresa 1001, 8 CCs da Controladoria, jan-set), % do custo de pessoal
            # fora Salários, Aviso Prév/Indeniz e Cursos; eventuais (licença-maternidade, ajuda de custo) e
            # contas abaixo de 0,05% ficam de fora. Pesos são normalizados no cálculo.
            "6010102001": 25.30,  # INSS
            "6010103003": 16.51,  # Assist Médica/Odonto
            "6010103002": 9.47,  # V.R./Alimentação
            "6010101012": 9.31,  # Férias
            "6010102002": 8.39,  # FGTS
            "6010101013": 6.58,  # 13º Salário
            "6010103006": 6.06,  # Vale Combustíveis
            "6010101009": 5.73,  # Gratific/Premiações
            "6010101003": 4.54,  # H.E./Quebra de Caixa
            "6010101016": 3.83,  # Prov de Grat/Abono
            "6010101002": 3.25,  # Adic Periculos/Insal
            "6010103010": 0.33,  # Bolsa de Estudo
            "6010103004": 0.16,  # Seguros de Vida
            "6010103005": 0.16,  # Auxílio Creche
            "6010101008": 0.08,  # Adicional Noturno
        },
        "Rateio da parte do multiplicador (encargos e benefícios) entre contas: {conta: peso}, normalizado; "
        "vazio = tudo na conta de encargos",
    ),
}

# code, nome, aplica multiplicador, multiplicador padrão
CONTRACT_TYPES = [
    ("CLT", "CLT", True, "1.8"),
    ("PJ", "Pessoa Jurídica", False, "1"),
    ("ESTAGIO", "Estágio", True, "1"),
    ("APRENDIZ", "Jovem Aprendiz", True, "1"),
    ("TEMPORARIO", "Temporário", True, "1"),
]

# code, nome, conta, modo
BENEFIT_TYPES = [
    ("AUX_CRECHE", "Auxílio Creche", "6010103005", "FLAG"),
    ("DEPENDENTES", "Total Dependentes", "6010103003", "PER_DEPENDENT"),
    ("VALE_TRANSPORTE", "Vale Transporte", "6010103001", "FLAG"),
    ("AUX_FACULDADE", "Auxílio Faculdade", "6010103010", "FLAG"),
    ("ADIC_NOTURNO", "Adicional Noturno", "6010101008", "FLAG"),
    ("ADIC_30", "Adicional 30%", "6010101002", "FLAG"),
    ("AUX_COMBUSTIVEL", "Auxílio Combustível", "6010103006", "AMOUNT"),
    ("AUX_FARMACIA", "Auxílio Farmácia", None, "FLAG"),  # conta "????" no template
    ("ABONO_ACT", "Abono ACT", None, "FLAG"),
    ("ESTACIONAMENTO", "Estacionamento", None, "FLAG"),
]

# Gestores de pacote (Cartilha 2027). (pacote, nome, empresa, rótulo)
PACKAGE_MANAGERS = [
    ("COMUNICACAO_MARKETING", "José Augusto", None, "Comunicação"),
    ("COMUNICACAO_MARKETING", "Monique Lasmar", None, "Marketing"),
    ("VIAGENS", "Christiane Pinheiro", None, None),
    ("PESSOAS", "Claudia Chunia", None, None),
    ("SERVICOS_TERCEIROS", "Maurício Godoy", None, None),
    ("INFRAESTRUTURA", "Monalisa Atem", "1001", "ATEM"),
    ("INFRAESTRUTURA", "Helder Barbosa", "2001", "REAM"),
    ("INFRAESTRUTURA", "Fernanda Takahama", None, "NAVE"),
    ("SEGURANCA_SEGUROS", "Carlos Jr", None, "Segurança"),
    ("SEGURANCA_SEGUROS", "Henrique Soares", None, "Seguros Patrimoniais"),
    ("OPERACOES_FINANCEIRAS", "Fabio Barcelos", None, None),
    ("TESOURARIA", "Christiane Pinheiro", None, None),
    ("TRIBUTARIO", "Alberto Silva", None, None),
    ("CONSUMO_EXPEDIENTE", "Jander Araújo", None, None),
    ("JURIDICO", "Diogo Coimbra", None, "Contencioso"),
    ("JURIDICO", "Lara Lebreiro", None, "Consultivo"),
    ("JURIDICO", "Raquel Maciel", None, "Comercial"),
    ("DTI", "Anderson Cruz", None, None),
    ("COMERCIAL", "Shirley Carvalho", "1001", "ATEM"),
    ("COMERCIAL", "Thiago Navarro", "2001", "REAM"),
    ("COMERCIAL", "Fernanda Takahama", None, "NAVE"),
    ("LOGISTICA_TRANSPORTE", "Kauan Okada", None, "Rodoviário"),
    ("LOGISTICA_TRANSPORTE", "Fernanda Takahama", None, "Fluvial / NAVE"),
]
