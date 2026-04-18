import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  use: {
    baseURL: 'http://localhost:8420',
    screenshot: 'on',
    viewport: { width: 1440, height: 900 },
  },
  outputDir: './screenshots',
});
