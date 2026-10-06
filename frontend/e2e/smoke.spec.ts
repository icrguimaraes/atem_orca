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
      await page.waitForLoadState("networkidle");
      await expect(page.getByText("Carregando…")).toHaveCount(0);
      await expectNoHorizontalOverflow(page);
      expect(errors, "erros no console").toEqual([]);
    });
  }
});

test("celular: menu abre, navega e fecha", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "só no celular");
  await login(page);
  const toggle = page.locator(".menu-toggle");
  await expect(toggle).toBeVisible();
  await expect(page.locator("#main-nav")).toBeHidden();
  await toggle.click();
  await expect(page.locator("#main-nav")).toBeVisible();
  await page.locator("#main-nav a", { hasText: "Cadastros" }).click();
  await expect(page).toHaveURL(/\/cadastros/);
  await expect(page.locator("#main-nav")).toBeHidden();
  await expectNoHorizontalOverflow(page);
});
