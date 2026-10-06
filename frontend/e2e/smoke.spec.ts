import { expect, test } from "@playwright/test";
import { expectNoHorizontalOverflow, login } from "./helpers";

const PAGES: [string, string][] = [
  ["/", "Painel"],
  ["/analise", "Análise orçamentária"],
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

test("celular: barra de abas e folha Mais", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "só no celular");
  await login(page);
  const tabbar = page.locator(".tabbar");
  await expect(tabbar).toBeVisible();
  await expect(page.locator(".sidebar nav")).toBeHidden();
  await tabbar.locator("a", { hasText: "Consolidação" }).click();
  await expect(page).toHaveURL(/\/consolidacao/);
  await tabbar.locator("button", { hasText: "Mais" }).click();
  await expect(page.locator(".sheet")).toBeVisible();
  await page.locator(".sheet-nav a", { hasText: "Cadastros" }).click();
  await expect(page).toHaveURL(/\/cadastros/);
  await expect(page.locator(".sheet")).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
});
