import { expect, test } from '@playwright/test';

// Runners are addressable from the host as ``cas-runner-<run-id-without-prefix>``
// (see ``agent/runner_spawn._container_name``). RunDetail derives the name
// client-side from ``run.run_id`` so we don't need an API round-trip. (#386)

test('RunDetail surfaces docker exec snippet for running runs', async ({ page }) => {
  await page.route('**/api/runs/run-canned/full', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        run_id: 'run-canned',
        status: 'running',
        coordinator_tasks: [],
      }),
    });
  });

  await page.goto('/runs/run-canned');
  await expect(page.getByText('docker exec -it cas-runner-canned bash')).toBeVisible();
  await expect(page.getByText('docker logs -f cas-runner-canned')).toBeVisible();
});

test('RunDetail hides docker exec snippet for completed runs', async ({ page }) => {
  await page.route('**/api/runs/run-done/full', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        run_id: 'run-done',
        status: 'completed',
        coordinator_tasks: [],
      }),
    });
  });

  await page.goto('/runs/run-done');
  await expect(page.getByText('docker exec -it cas-runner-done bash')).not.toBeVisible();
});
