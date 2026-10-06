# Design system — Coral Stay

> Referência visual do sistema de orçamento ATEM. Os tokens estão em `frontend/src/styles.css` (`:root`).
> Adaptações para um app de dados (seção "Aplicação no ATEM" no fim).

## Overview
A warm, inviting design system built around trust and belonging. Coral Stay uses a signature coral-pink accent against soft, neutral backgrounds to create an emotional connection with users. The aesthetic is rounded, friendly, and photographic — designed to make every interaction feel personal, approachable, and effortlessly welcoming.

## Colors
- **Primary** (#FF5A5F): Call-to-action buttons, hearts, key highlights — Rausch Coral
- **Primary Hover** (#E04E52): Hover and pressed state for primary coral actions
- **Secondary** (#00A699): Success states, verified badges, secondary accents — Kazan Teal
- **Neutral** (#767676): Body text, secondary labels, icons — Foggy Gray
- **Background** (#FFFFFF): Primary page background, clean and open
- **Surface** (#F7F7F7): Card backgrounds, search bar fills, section dividers
- **Text Primary** (#222222): Headlines, listing titles, primary content — Hof Dark
- **Text Secondary** (#717171): Descriptions, metadata, supporting text
- **Border** (#DDDDDD): Dividers, card outlines, input borders — Babu Light Gray
- **Success** (#008A05): Superhost badges, positive confirmations
- **Warning** (#E07912): Pricing alerts, availability warnings
- **Error** (#C13515): Booking errors, validation failures, cancellation

## Typography
- **Display Font**: Nunito Sans — loaded from Google Fonts
- **Body Font**: DM Sans — loaded from Google Fonts
- **Code Font**: JetBrains Mono — loaded from Google Fonts

Nunito Sans serves as the display face, bringing a rounded, humanist warmth to headlines and marketing copy at weights 700 and 800. DM Sans handles all body text, UI labels, and navigation at weights 400, 500, and 600. The typography system favors bold, large titles with generous line heights to feel open and scannable. Letter-spacing is kept at default or slightly loose (0.01em) for smaller text to maintain readability.

- **Hero Title**: Nunito Sans 48px/56px, weight 800, tracking -0.01em
- **Section Title**: Nunito Sans 32px/40px, weight 700
- **Card Title**: Nunito Sans 22px/28px, weight 700
- **Subtitle**: DM Sans 18px/24px, weight 600
- **Body Large**: DM Sans 16px/24px, weight 400
- **Body**: DM Sans 14px/20px, weight 400
- **Body Small**: DM Sans 12px/16px, weight 400
- **Label**: DM Sans 12px/16px, weight 600, tracking 0.02em, uppercase
- **Code**: JetBrains Mono 14px/20px, weight 400

## Elevation
Shadow strategy is subtle and warm, using slightly tinted shadows to feel less cold than pure black. Level 1 (0 1px 2px rgba(0,0,0,0.08), 0 4px 12px rgba(0,0,0,0.05)) is used for cards and listing tiles. Level 2 (0 2px 4px rgba(0,0,0,0.08), 0 8px 24px rgba(0,0,0,0.12)) is used for modals, date pickers, and dropdown menus. Level 3 (0 6px 20px rgba(0,0,0,0.12), 0 16px 40px rgba(0,0,0,0.16)) is reserved for the sticky booking bar and full-screen overlays. On hover, cards transition from Level 1 to Level 2 with a 200ms ease transition.

## Components
- **Buttons**: Primary uses #FF5A5F fill, white text, 14px/24px padding, 8px border-radius, weight 600 at 16px. Large variant is 48px height with 24px/32px padding. Secondary has 1px #222222 border, transparent fill, #222222 text. Ghost button is text-only in #222222. All buttons darken 8% on hover with 150ms transition.
- **Cards**: White background, 12px border-radius, 1px #DDDDDD border. Image fills top with 12px top border-radius. Content area has 16px padding. Title in Nunito Sans 18px weight 700, subtitle in DM Sans 14px #717171. Heart icon positioned absolute top-right at 12px offset. Hover lifts with Level 2 shadow and subtle translateY(-2px).
- **Inputs**: 48px height, 1px #DDDDDD border, 8px border-radius, 12px horizontal padding. Focused state: 2px #222222 border. Label above input in DM Sans 12px weight 600. Error state: 2px #C13515 border with error message in #C13515 below.
- **Chips**: Pill-shaped (9999px radius), 1px #DDDDDD border, transparent background, DM Sans 14px weight 600, 8px/16px padding. Selected state: #222222 background, white text, no border. Filter chips include leading icon at 16px.
- **Lists**: Listing rows use horizontal layout with 56px thumbnail, 16px gap. Title in Nunito Sans 16px weight 600, description in DM Sans 14px #717171, price in DM Sans 16px weight 700. Separator is 1px #DDDDDD with full bleed.
- **Checkboxes**: 24px rounded square with 4px border-radius. Unchecked: 2px #717171 border. Checked: #222222 fill with white checkmark. Animated with 150ms ease.
- **Tooltips**: White background, 8px border-radius, Level 2 shadow, #222222 text at 14px weight 400, 12px/16px padding. Arrow in matching white.
- **Navigation**: Sticky header 80px height, white background, Level 1 shadow on scroll. Logo left, search bar center (48px height, 1px #DDDDDD border, 9999px radius with icon sections), user menu right. Mobile bottom navigation with 5 icons at 56px height.
- **Search**: Central search bar divided into segments (Location | Check in | Check out | Guests) with vertical dividers. 9999px border-radius, 1px #DDDDDD border, Level 1 shadow. Expands into a full search panel on focus. Small variant is single-line with magnifying glass icon.

## Spacing
- Base unit: 8px
- Scale: 4px, 8px, 12px, 16px, 24px, 32px, 40px, 48px, 64px, 80px
- Component padding: 16px standard, 24px for cards and sections
- Section spacing: 48px between major sections, 24px between related groups
- Container max width: 1280px with 24px side margins (mobile), 40px (desktop)
- Card grid gap: 24px

## Border Radius
- 4px: Checkboxes, small tags
- 8px: Buttons, inputs, standard cards
- 12px: Listing cards, image containers, modals
- 16px: Large panels, search expansion, bottom sheets
- 9999px: Search bar, pills, chips, avatar circles

## Do's and Don'ts
- Do use photography as a primary design element — large, high-quality images drive engagement
- Do maintain warmth through the coral accent and rounded shapes
- Don't overuse the coral color — reserve it for primary CTAs and hearts only
- Do use #222222 as the dominant text color for a warm, non-harsh reading experience
- Don't use pure black (#000000) for text — it feels too cold for this system
- Do keep card layouts consistent with image-top, content-bottom pattern
- Don't use sharp corners — minimum 4px border-radius on all interactive elements
- Do provide generous touch targets (48px minimum) for all booking-critical actions
- Don't hide pricing — always show cost prominently with DM Sans weight 700

## Aplicação no ATEM

### Aninhamento e modebar

- Elemento com borda dentro de um `.card` (stat, tabela, sub-card, resumo de filtros) usa `--radius-sm` (8 px) e sem sombra própria: cantos internos menores que os externos (12 px).
- A *modebar* do Plotly no desktop aparece só no hover, sem fundo (ícones em `--muted`); no celular não existe.

### Tema claro/escuro

- Três opções no rodapé da barra lateral e na folha "Mais" do celular: **Auto** (segue o sistema), **Claro**, **Escuro** (`theme.tsx`, preferência em `localStorage["atem.theme"]`, aplicada em `index.html` antes do React para não piscar).
- CSS: os tokens escuros existem em dois seletores — `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) }` (automático) e `:root[data-theme="dark"]` (forçado). Todo token novo precisa entrar nos dois. O Plotly acompanha pela observação de `data-theme`.

### Largura e rótulos de extremos

- Conteúdo em até **1600 px** (`.content`), com 40 px de margem; em monitores largos o espaço vazio fica à direita, não dentro dos cards.
- Gráfico sem rótulo em cada ponto (linhas e barras mensais) mostra **sempre o maior e o menor valor** da série: Plotly via `_extreme_labels` (texto no ponto, cor da série); SVG via `ExtremeLabels` (contorno na cor da superfície para legibilidade). O mínimo considera só meses com valor.
- Gráficos SVG mensais são desenhados na largura real do container (`useWidth`), em escala 1:1: texto e barras não esticam.

### Marca e celular

- **Logo**: `frontend/public/brand/logo.png` (tema claro) e `logo-dark.png` (texto branco, tema escuro), fundo transparente, usadas por `components/Brand.tsx` na barra lateral (56 px), na barra do celular (34 px) e no login (72 px); o CSS escolhe a variante pelo tema. Favicon em `brand/favicon.png`. A logo já traz "atem GRUPO", então o texto ao lado é só "Orçamento 2027".
- **Celular (≤ 860 px)**: barra superior fixa só com a marca; navegação numa **barra de abas inferior** (Painel, Análise, Consolidação e "Mais"), ícones de linha com o item ativo em coral. "Mais" abre uma folha inferior com o restante da navegação, o nome do usuário, alterar senha e sair; fecha ao navegar ou tocar fora. Conteúdo com espaço inferior para a barra (`env(safe-area-inset-bottom)`). Filtros de página (`components/FilterBar.tsx`): no desktop, selects em linha; no celular, resumo em grade com rótulos (faixa cinza, 2 colunas, campo longo em largura total, campo ativo com borda escura) e, ao tocar em qualquer campo, folha inferior com todos os filtros, "Limpar" e "Aplicar (n)" — os valores só são aplicados ao confirmar. Cards de indicador 2 por linha (o último, se ímpar, ocupa a linha); tabelas rolam dentro de `.table-wrap`.
- **Gráficos Plotly no celular** (`components/PlotlyChart.tsx`, detecção por `(hover: none)` ou ≤ 860 px): sem *modebar* (em tela de toque ela não some e cobre o gráfico), sem arrastar/zoom (o dedo rola a página), fonte 11 px. Barras verticais com categorias em texto viram horizontais (nome à esquerda, valor no fim da barra, altura proporcional ao número de linhas); barras divergentes crescem todas para a direita em valor absoluto (cor e sinal no texto indicam aumento/redução); o eixo de valor fica só com a grade quando o valor já está escrito na barra; rótulos de categoria truncados em 22 caracteres. As figuras continuam vindo do backend sem alteração — a adaptação é só de apresentação.

- **Navegação:** mantida a barra lateral (o sistema tem 9+ seções; um header de 80px não comporta). Ela é branca com borda #DDDDDD; o item ativo usa o estilo de chip selecionado (#222222, texto branco, pílula). No celular vira faixa horizontal no topo.
- **Coral:** só em botões primários (Enviar, Salvar, Confirmar), marca e seleção de texto. Abas, foco de campos e filtros usam #222222.
- **Warning:** #E07912 em bordas e barras; em texto pequeno usa-se #B4600E (contraste ≥ 4,5:1 no branco).
- **Densidade:** campos de formulário com 48px; campos dentro de tabelas e grades mensais ficam com 34px para caber 12 meses na tela. Botões padrão com 44px e botões pequenos (ações de linha) com 32px.
- **Gráficos:** a paleta categórica validada (azul, laranja, verde-água) foi mantida para não confundir séries com o coral de ação. Série "ano anterior / base / anualizado" usa o cinza neutro `--series-past` (`SERIES.past`; `COLOR_PAST` no Plotly), para a série do ano em foco (azul) se destacar; o laranja fica para categorias (CAPEX, encargos, desligamentos). Rankings horizontais (`TopBars`) em degradê do azul, do maior (cor cheia) ao menor; barras de 22 px e 10 px de respiro entre linhas.
- **Painel** (modelo: painel de Custeio em Power BI) é o único painel do sistema: as antigas páginas "Orçamento OPEX/CAPEX/Pessoal" viraram o filtro **Tipo** (OPEX, CAPEX, Pessoal); `/orcamento`, `/capex` e `/pessoal` redirecionam para `/?tipo=…`; a fila "Pacotes aguardando sua validação" e o botão "Simular cenário de pessoal" ficam no Painel; o orçamento de cada CC continua em `/orcamento/:cc`, `/capex/:cc` e `/pessoal/:cc` (abertos por "Suas tarefas" ou pela matriz da Consolidação). **Filtros somam**: os chips de Ano (Todos + 2025, 2026, 2027…), Mês (Todos + Jan..Dez) e Tipo são seleção múltipla e todos os números somam o período escolhido. **Duas séries**: realizado (azul) e orçado (verde: orçamento de referência importado e, no ano do ciclo, o orçamento proposto da consolidação). Com realizado no período ele é a série principal; só com o ano do ciclo, a principal é o "Orçamento 2027" (verde, inclusive no mapa de calor). A base de comparação é uma só: o ano anterior (cinza) ou, sem ela, o orçado do período; orçamento do ciclo × ano anterior incompleto usa o ano anterior **anualizado**. **Comparação é opção**: "Comparar com o ano anterior" (só com um ano selecionado e o anterior carregado) e "Mesmo período (até SET)" (base limitada ao último mês fechado do ano em foco), as duas ligadas por padrão; a nota "Selecionado: Realizado 2026 até SET · base: 2025 até SET" diz o que está em tela. KPIs; depois os blocos **Comparativo mensal** (colunas lado a lado com degradê do maior para o menor em cada série e o valor em cada barra quando há espaço), **tabela com drill-down** pacote GMD → conta → centro de custo (`components/DrillTable.tsx`, `/dashboard/breakdown`; colunas valor, AV %, base, AV %, variação e variação % com semáforo pelos limiares do ciclo: vermelho acima de `alert.growth_pct`, amarelo queda maior que `alert.reduction_pct`, verde na faixa; bolinha e número em colunas fixas; ordenação; "Recolher tudo" é o drill-up), **Total acumulado**, maiores CCs e contas (`TopBars`) e mapa de calor. Os blocos de pacotes GMD em barras, variação por pacote e maiores aumentos e reduções por conta saíram (06/10/2026: não interessam à gestão; o detalhe por pacote está na tabela). Sem card de prazos. **"Organizar painel"** deixa o usuário subir/descer cada bloco (CSS `order`, `localStorage["atem.painel.order"]`). Espaço entre visuais: 28 px. A manutenção da base (qualidade, importações, versões) fica no fim.
- **Painel 2** (`/painel-2`, teste): o mesmo Painel com os gráficos em Plotly (figuras montadas no backend, mesma paleta, degradê e rótulos; colunas mostram o valor quando cabe — `uniformtext` esconde o que não cabe), com zoom/PNG pela barra do Plotly e clique para filtrar (mês, centro de custo). O mapa de calor continua em HTML (o bundle básico do Plotly não tem heatmap). Serve para escolher entre SVG e Plotly; a versão preterida sai.
- **Cores dos gráficos** (Power BI de Custeio): **verde-água `#4fa894` é sempre orçamento** (orçado de referência e orçamento proposto), azul `#4472c4` é realizado e roxo `#8a6bbf` (`--series-past`) é o ano anterior. Rótulos de série usam o tom escuro da própria cor (`--series-1-ink` `#3a64b4`, `--series-3-ink` `#2b7a68`, `--series-past-ink` `#6a4c9c`; no escuro, tons claros), nunca cinza. Eixos e legendas usam `--axis` (`#4a4a4a`; `#cfcfcf` no escuro), mais forte que `--muted`. Mapa de calor em rampa azul (realizado) ou verde (`--seqg-lo/hi`, orçamento). No escuro `#5b8ad6` / `#6fbfb0`. Rankings e barras pareadas escalam pelo maior valor real (a maior barra ocupa a trilha inteira).
- **Modo escuro:** derivado dos mesmos papéis (fundo #121212, superfície #1C1C1C, coral #FF6B70).
- **Fotografia:** não se aplica (sistema de dados); a "foto" de cada tela é o gráfico principal.
