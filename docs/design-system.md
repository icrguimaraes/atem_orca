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

### Marca e celular

- **Logo**: `frontend/public/brand/logo.svg` (ou `.png`) é usada por `components/Brand.tsx` na barra lateral, na barra superior do celular e no login; sem o arquivo, monograma "A" em coral.
- **Celular (≤ 860 px)**: barra superior fixa só com a marca; navegação numa **barra de abas inferior** (Painel, OPEX, CAPEX, Pessoal e "Mais"), ícones de linha com o item ativo em coral. "Mais" abre uma folha inferior com o restante da navegação, o nome do usuário, alterar senha e sair; fecha ao navegar ou tocar fora. Conteúdo com espaço inferior para a barra (`env(safe-area-inset-bottom)`). Filtros em largura total; cards de indicador 2 por linha (o último, se ímpar, ocupa a linha); tabelas rolam dentro de `.table-wrap`.
- **Gráficos Plotly no celular** (`components/PlotlyChart.tsx`, detecção por `(hover: none)` ou ≤ 860 px): sem *modebar* (em tela de toque ela não some e cobre o gráfico), sem arrastar/zoom (o dedo rola a página), fonte 11 px. Barras verticais com categorias em texto viram horizontais (nome à esquerda, valor no fim da barra, altura proporcional ao número de linhas); barras divergentes crescem todas para a direita em valor absoluto (cor e sinal no texto indicam aumento/redução); o eixo de valor fica só com a grade quando o valor já está escrito na barra; rótulos de categoria truncados em 22 caracteres. As figuras continuam vindo do backend sem alteração — a adaptação é só de apresentação.

- **Navegação:** mantida a barra lateral (o sistema tem 9+ seções; um header de 80px não comporta). Ela é branca com borda #DDDDDD; o item ativo usa o estilo de chip selecionado (#222222, texto branco, pílula). No celular vira faixa horizontal no topo.
- **Coral:** só em botões primários (Enviar, Salvar, Confirmar), marca e seleção de texto. Abas, foco de campos e filtros usam #222222.
- **Warning:** #E07912 em bordas e barras; em texto pequeno usa-se #B4600E (contraste ≥ 4,5:1 no branco).
- **Densidade:** campos de formulário com 48px; campos dentro de tabelas e grades mensais ficam com 34px para caber 12 meses na tela. Botões padrão com 44px e botões pequenos (ações de linha) com 32px.
- **Gráficos:** a paleta categórica validada (azul, laranja, verde-água) foi mantida para não confundir séries com o coral de ação.
- **Modo escuro:** derivado dos mesmos papéis (fundo #121212, superfície #1C1C1C, coral #FF6B70).
- **Fotografia:** não se aplica (sistema de dados); a "foto" de cada tela é o gráfico principal.
