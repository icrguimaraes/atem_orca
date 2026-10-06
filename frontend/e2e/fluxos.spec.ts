import { expect, test } from "@playwright/test";
import { login } from "./helpers";

test("análise: filtros, drill-down e controles dos gráficos", async ({ page }) => {
  await login(page);
  await page.goto("/analise");
  await expect(page.locator(".plotly-chart .main-svg").first()).toBeVisible({ timeout: 30_000 });
  await page.selectOption("select[aria-label='Dimensão']", "account");
  await page.click("button:has-text('%')");
  await page.click("button:has-text('Top 20')");
  await expect(page.locator(".plotly-chart .main-svg").first()).toBeVisible();
  const chart = page.locator(".plotly-chart").nth(1);
  await chart.scrollIntoViewIfNeeded();
  const bars = chart.locator(".bars .point path");
  // escolhe uma barra com valor (altura > 0); séries sem dados geram barras de altura zero
  let box: { x: number; y: number; width: number; height: number } | null = null;
  for (let i = 0; i < (await bars.count()) && !box; i++) {
    const b = await bars.nth(i).boundingBox();
    if (b && b.height > 2) box = b;
  }
  test.skip(!box, "sem barras com valor no comparativo (base sem orçamento lançado)");
  if (box) {
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.up();
    await expect(page.locator(".breadcrumb")).toContainText("Conta contábil");
    await page.click(".breadcrumb button:has-text('Visão geral')");
    await expect(page.locator(".breadcrumb")).toHaveCount(0);
  }
});

test("consolidação: exportar Excel e abrir o CC a partir da matriz", async ({ page }) => {
  await login(page);
  await page.goto("/consolidacao");
  await expect(page.getByText("Pontos de atenção")).toBeVisible();
  const [download] = await Promise.all([page.waitForEvent("download"), page.click("text=Exportar Excel")]);
  expect(download.suggestedFilename()).toMatch(/^Orcamento_\d{4}_v.+\.xlsx$/);
  await expect(page.getByText("Carregando…")).toHaveCount(0);
  const first = page.locator(".table a .badge").first(); // matriz CC × módulos (links para o CC)
  test.skip((await first.count()) === 0, "matriz CC × módulos vazia (sem centros de custo)");
  {
    await first.scrollIntoViewIfNeeded();
    await first.click();
    await expect(page).toHaveURL(/\/(orcamento|capex|pessoal)\/\d+/);
  }
});

test("menu: alterar senha valida confirmação", async ({ page }) => {
  await login(page);
  await page.click("button:has-text('Alterar senha')");
  const modal = page.locator(".modal");
  await expect(modal).toBeVisible();
  await modal.locator("input[type=password]").nth(0).fill("x");
  await modal.locator("input[type=password]").nth(1).fill("NovaSenha123");
  await modal.locator("input[type=password]").nth(2).fill("Diferente123");
  await expect(modal.locator("button:has-text('Salvar')")).toBeEnabled();
  await modal.locator("button:has-text('Salvar')").click();
  await expect(page.locator(".modal .alert")).toContainText("confirmação");
  await page.click(".modal button:has-text('Cancelar')");
});

test("orçamento OPEX: baixar o template preenchido do CC", async ({ page }) => {
  await login(page);
  await page.goto("/orcamento");
  await expect(page.getByText("Carregando…")).toHaveCount(0);
  const first = page.locator(".table a").first();
  test.skip((await first.count()) === 0, "lista OPEX vazia (sem centros de custo)");
  await first.click();
  await expect(page).toHaveURL(/\/orcamento\/\d+/);
  const [download] = await Promise.all([page.waitForEvent("download"), page.click("button:has-text('Baixar template (Excel)')")]);
  expect(download.suggestedFilename()).toMatch(/^Template_OPEX_\d{4}_\d+\.xlsx$/);
});

test("tema: escolha persiste e volta ao automático", async ({ page }) => {
  await login(page);
  await page.locator(".theme-switch button", { hasText: "Escuro" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.locator(".theme-switch button", { hasText: "Auto" }).click();
  await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.+/);
});
