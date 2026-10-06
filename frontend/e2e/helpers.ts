import { expect, type Page } from "@playwright/test";

export const EMAIL = process.env.E2E_EMAIL ?? "icaro@icaroguimaraes.com";
export const PASSWORD = process.env.E2E_PASSWORD ?? "Admin@12345";

export async function login(page: Page) {
  await page.goto("/login");
  await page.fill("input[type=email]", EMAIL);
  await page.fill("input[type=password]", PASSWORD);
  await page.click("button");
  await expect(page.getByText("Painel").first()).toBeVisible();
}

/** Nenhum elemento fora da tabela rolável pode passar da largura da janela (regra do design system). */
export async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow, "página não pode ter rolagem horizontal").toBe(false);
}
