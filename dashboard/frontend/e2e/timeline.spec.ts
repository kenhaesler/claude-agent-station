import { expect, test } from '@playwright/test';

test('Timeline tab loads and filters', async ({ page }) => {
  await page.route('**/api/runs/run-canned/timeline*', async (route) => {
    const url = new URL(route.request().url());
    const kinds = url.searchParams.get('kinds');
    const allEvents = [
      { t: '2026-05-13T15:00:00Z', kind: 'lifecycle', event: 'run_start',
        source: 'runs', source_id: 'run-canned', agent: null, data: {} },
      { t: '2026-05-13T15:01:00Z', kind: 'tool', event: 'tool.bash.ok',
        source: 'audit_log', source_id: '1', agent: 'lead', data: { exit_code: 0 } },
    ];
    const filtered = kinds
      ? allEvents.filter((e) => kinds.split(',').includes(e.kind))
      : allEvents;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        run_id: 'run-canned',
        events: filtered,
        next_cursor: null,
        has_more: false,
      }),
    });
  });

  await page.goto('/runs/run-canned');
  await page.getByRole('button', { name: 'Timeline' }).click();
  await expect(page.locator('.event.lifecycle')).toBeVisible();
  await expect(page.locator('.event.tool')).toBeVisible();

  // Click the `tool` chip to deactivate it.
  await page.getByRole('button', { name: 'tool', exact: true }).click();
  await expect(page.locator('.event.tool')).toHaveCount(0);
  await expect(page.locator('.event.lifecycle')).toBeVisible();
});
