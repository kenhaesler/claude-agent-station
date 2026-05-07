import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [tailwindcss(), svelte()],
  test: {
    environment: 'node',
    // Only run vitest specs under src/. e2e/*.spec.ts files are
    // Playwright tests and use a different runner.
    include: ['src/**/*.{test,spec}.{ts,tsx,js}'],
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8420',
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
