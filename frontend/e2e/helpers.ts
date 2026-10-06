import { expect, type Page } from "@playwright/test";

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Defina ${name} para rodar os testes de interface (usuário com acesso a todas as páginas)`);
  return value;
}

export const EMAIL = required("E2E_EMAIL");
export const PASSWORD = required("E2E_PASSWORD");

export async function login(page: Page) {
  await page.goto("/login");
  await page.fill("input[type=email]", EMAIL);
  await page.fill("input[type=password]", PASSWORD);
  await page.click("button[type=submit], form button");
  await expect(page.locator("nav a", { hasText: "Painel" })).toBeVisible();
}

/** Nenhum elemento fora da tabela rolável pode passar da largura da janela (regra do design system). */
export async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow, "página não pode ter rolagem horizontal").toBe(false);
}
