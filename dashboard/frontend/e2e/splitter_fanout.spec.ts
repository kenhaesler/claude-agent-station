/**
 * RunDetail fan-out panel for splitter parent runs (#391).
 *
 * Mocks the tree + full-context endpoints and asserts the panel renders
 * the linked sub-runs. The test deliberately stubs both routes so the
 * frontend wiring is verified in isolation from backend availability.
 */
import { expect, test } from '@playwright/test';

test('RunDetail shows fan-out panel for parent run', async ({ page }) => {
  await page.route('**/api/runs/run-parent-1/tree', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        run_id: 'run-parent-1',
        run_kind: 'split-decision',
        sub_runs: [
          { run_id: 'run-sub-a', run_kind: 'sub-of-27', verdict: 'APPROVE', status: 'success' },
          { run_id: 'run-sub-b', run_kind: 'sub-of-27', verdict: 'PR', status: 'success' },
        ],
      }),
    });
  });
  await page.route('**/api/runs/run-parent-1/full', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        run: {
          id: 1,
          run_id: 'run-parent-1',
          status: 'success',
          run_kind: 'split-decision',
        },
        coordinator_tasks: [],
        coordinator_messages: [],
        queue_item: null,
        queue_items: [],
        plan: null,
        project_repo: null,
        intelligence_decisions: [],
        team_summary: null,
      }),
    });
  });

  await page.goto('/runs/run-parent-1');
  await expect(page.getByText('Fan-out')).toBeVisible();
  await expect(page.getByText('run-sub-a')).toBeVisible();
  await expect(page.getByText('run-sub-b')).toBeVisible();
});
