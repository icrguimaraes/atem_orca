/**
 * "Como funciona" de cada tela (12/11/2026, mesmo padrão do MRP, do portal de Subvenções e da Prévia de Resultados):
 * o que é a tela, o que faz, onde afeta, como usar e o que é bom saber. Texto para o usuário e para apresentar o
 * sistema. Uma entrada por rota; rotas com parâmetro usam ":id" (ex.: "/projecao/:id").
 */

export interface Ajuda {
  titulo: string;
  /** uma frase: a tela em uma linha */
  resumo: string;
  oQueFaz: string[];
  ondeAfeta: string[];
  /** passos, em ordem */
  comoUsar: string[];
  bomSaber?: string[];
}

/** Caminho do orçamento, mostrado no rodapé de todo tutorial (onde a tela está no fluxo). */
export const FLUXO = [
  "Cargas e cadastros",
  "Orçamento das áreas",
  "Validação e apontamentos",
  "Consolidação e revisões",
  "Acompanhamento e defesa",
];

export const AJUDA: Record<string, Ajuda & { etapa?: number }> = {
  "/projecao": {
    titulo: "Projeção",
    etapa: 4,
    resumo: "Lista os centros de custo com o realizado do ano, a projeção dos meses que faltam e o desvio contra o orçado.",
    oQueFaz: [
      "Soma, por centro de custo, o realizado dos meses fechados e a projeção dos meses abertos (OPEX, Pessoal e CAPEX).",
      "Compara a projeção do ano (realizado + projeção) com o orçado do ano e mostra o desvio.",
      "Mostra quem gravou a projeção na tela pela última vez e quando.",
    ],
    ondeAfeta: ["Não altera nada: é só leitura."],
    comoUsar: [
      "Escolha a empresa e a situação em “Gravação”, ou busque o centro de custo.",
      "Confira o desvio de cada centro de custo contra o orçado.",
      "Clique no centro de custo para abrir a grade e digitar a projeção.",
    ],
    bomSaber: [
      "O gestor vê só os centros de custo dele; a Controladoria vê todos os que têm números no ano.",
      "Os meses fechados vão até o último mês de realizado carregado de cada empresa.",
    ],
  },
  "/projecao/:id": {
    titulo: "Projeção do centro de custo",
    etapa: 4,
    resumo: "Onde o gestor digita a projeção dos meses que faltam, conta por conta, e grava depois de revisar.",
    oQueFaz: [
      "Mostra o realizado dos meses fechados, travado, e abre os meses que faltam para digitar.",
      "Preenche os meses abertos com a projeção vigente e marca a origem: P (importada da planilha) ou G (gravada na tela). O orçado do mês aparece só como referência, embaixo do campo.",
      "Antes de gravar, compara a projeção atual com a nova por mês, por natureza e célula a célula.",
    ],
    ondeAfeta: [
      "“Confirmar e gravar” cria uma nova versão da projeção da empresa no ano, trocando só as células alteradas deste centro de custo. A versão anterior fica no histórico.",
      "A nova projeção vale na hora no Painel, no Rastro e em Projetos.",
      "Cada gravação vai para a auditoria com o antes e o depois, e aparece em “Histórico de gravações”.",
    ],
    comoUsar: [
      "Digite os valores nos meses abertos; use “Acrescentar conta” para uma conta que não está na grade.",
      "Clique em “Revisar e gravar” e confira o que mudou.",
      "Escreva um comentário, se quiser, e clique em “Confirmar e gravar”.",
    ],
    bomSaber: [
      "Quando o realizado de um mês entra (KSB1), a projeção daquele mês sai sozinha e o mês trava.",
      "“Foco nos meses abertos” junta os meses fechados numa coluna; “Ano completo” mostra mês a mês.",
      "Aceita valores negativos (ex.: -500) e o formato 1.234,56.",
      "Perfil de consulta só lê. Se outra pessoa gravar antes de você, o sistema pede para recarregar.",
    ],
  },
};

/** Tutorial da rota: busca exata e, se não achar, o mesmo caminho com o último trecho numérico como ":id". */
export function ajudaDaRota(caminho: string): (Ajuda & { etapa?: number }) | null {
  const limpo = caminho.replace(/\/+$/, "") || "/";
  if (AJUDA[limpo]) return AJUDA[limpo];
  const generico = limpo.replace(/\/\d+(?=\/|$)/g, "/:id");
  return AJUDA[generico] ?? null;
}
