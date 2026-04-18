import { test, expect } from '@playwright/test';

test('Dashboard page loads', async ({ page }) => {
  await page.goto('/');
  const root = page.locator('[data-testid="command-center"]');
  await expect(root).toBeVisible({ timeout: 5000 });
  await page.screenshot({ path: 'screenshots/01-dashboard.png', fullPage: true });
});

test('Agents page (idle state)', async ({ page }) => {
  await page.goto('/agents');
  // /agents redirects to /agent-teams. Idle copy is the off-duty heading.
  const heading = page.getByRole('heading', { name: /off-duty|team/i });
  await expect(heading).toBeVisible({ timeout: 5000 });
  await page.screenshot({ path: 'screenshots/02-agents-idle.png', fullPage: true });
});

test('Runs page loads', async ({ page }) => {
  await page.goto('/runs');
  await expect(page.locator('.animate-fade-in').first()).toBeVisible({ timeout: 5000 });
  await page.screenshot({ path: 'screenshots/03-runs.png', fullPage: true });
});

test('Queue page loads', async ({ page }) => {
  await page.goto('/queue');
  await expect(page.locator('.animate-fade-in-up, .animate-fade-in').first()).toBeVisible({ timeout: 5000 });
  await page.screenshot({ path: 'screenshots/04-queue.png', fullPage: true });
});

test('Settings page loads', async ({ page }) => {
  await page.goto('/settings');
  // Settings exposes tabs — assert at least one tab button is visible.
  const tab = page.getByRole('button', { name: /general|models|services|auth|prompts/i }).first();
  await expect(tab).toBeVisible({ timeout: 5000 });
  await page.screenshot({ path: 'screenshots/05-settings.png', fullPage: true });
});
