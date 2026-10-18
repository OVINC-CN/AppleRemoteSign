import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  timeout: 45000,
  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'retain-on-failure',
    ...devices['Desktop Chrome'],
  },
  webServer: [
    {
      command:
        '../../.venv/bin/uvicorn tests.browser_server:app --app-dir ../.. --host 127.0.0.1 --port 18765',
      url: 'http://127.0.0.1:18765/health',
      reuseExistingServer: false,
    },
    {
      command:
        process.env.PLAYWRIGHT_SKIP_BUILD === '1'
          ? 'npm run preview -- --port 4173 --strictPort'
          : 'npm run build && npm run preview -- --port 4173 --strictPort',
      url: 'http://127.0.0.1:4173',
      reuseExistingServer: false,
    },
  ],
});
