import { defineConfig } from "@playwright/test";

// Testes de interface contra um app já no ar (local ou Railway): E2E_BASE_URL, E2E_EMAIL e E2E_PASSWORD.
// Local: scripts/dev-db.sh, uvicorn com FRONTEND_DIST apontando para dist, depois `npm run e2e`.
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8077",
    ignoreHTTPSErrors: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { viewport: { width: 1440, height: 900 } } },
    { name: "mobile", use: { viewport: { width: 390, height: 844 } }, testMatch: /smoke/ },
  ],
});
