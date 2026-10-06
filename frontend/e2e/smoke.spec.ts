import { expect, test } from "@playwright/test";
import { expectNoHorizontalOverflow, login } from "./helpers";

const PAGES: [string, string][] = [
  ["/", "Painel"],
  ["/analise", "Análise orçamentária"],
  ["/orcamento", "Orçamento OPEX"],
  ["/capex", "Orçamento CAPEX"],
  ["/pessoal", "Orçamento de Pessoal"],
  ["/consolidacao", "Consolidação e exportação"],
  ["/importacoes", "Importação de dados"],
  ["/cadastros", "Cadastros"],
  ["/ciclo", "Orçamento"],  // h1 é o nome do ciclo
];

test.describe("todas as páginas abrem sem erro e sem overflow", () => {
  for (const [path, title] of PAGES) {
    test(`${path}`, async ({ page }) => {
      const errors: string[] = [];
      page.on("console", (m) => m.type() === "error" && !/fonts|ERR_|favicon/.test(m.text()) && errors.push(m.text()));
      await login(page);
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toContainText(title);
      await page.waitForTimeout(800);
      await expectNoHorizontalOverflow(page);
      expect(errors, "erros no console").toEqual([]);
    });
  }
});
