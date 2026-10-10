import { test as base, expect } from '@playwright/test';
import { buildHandoffProject, buildProjects, openAppsTab, setupOps, emit } from './fixtures/ops.js';
import path from 'path';

const test = base.extend({
  page: async ({ page }, runTest) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error' && !message.text().includes('Error creating WebGL context') && !message.text().startsWith('Failed to load resource:')) {
        errors.push(message.text());
      }
    });
    await runTest(page);
    expect(errors).toEqual([]);
  },
});

const SCREENSHOT_DIR = path.resolve('test-results/visual');

const VIEWPORTS = [
  { name: '1440x900', width: 1440, height: 900 },
  { name: '1366x768', width: 1366, height: 768 },
  { name: '768x1024', width: 768, height: 1024 },
  { name: '375x667', width: 375, height: 667 },
  { name: '375x320', width: 375, height: 320 },
];

test.describe('Visual Verification Across Viewports and States', () => {
  for (const vp of VIEWPORTS) {
    test(`Apps Board visual capture at ${vp.name}`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await setupOps(page);
      await openAppsTab(page);

      // Verify no outer page scrolling
      const pageScroll = await page.evaluate(() => window.scrollY);
      expect(pageScroll).toBe(0);

      // Capture full viewport
      await page.screenshot({
        path: path.join(SCREENSHOT_DIR, `board-${vp.name}.png`),
        fullPage: false,
      });

      // Verify internal scrolling if content overflows
      const board = page.locator('.ops-board');
      const isScrollable = await board.evaluate((el) => el.scrollHeight > el.clientHeight);
      if (isScrollable) {
        await board.evaluate((el) => { el.scrollTop = el.scrollHeight; });
        await page.screenshot({
          path: path.join(SCREENSHOT_DIR, `board-${vp.name}-scrolled-bottom.png`),
          fullPage: false,
        });
      }
    });
  }

  test('Visual capture of Stop Confirmation Dialog', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await setupOps(page);
    await openAppsTab(page);

    const guestbook = page.getByRole('article', { name: 'guestbook', exact: true });
    await guestbook.getByRole('button', { name: '중지', exact: true }).click();
    await expect(guestbook.getByText(/모든 서비스를 중지할까요\?.*DB 데이터는 보존돼요/)).toBeVisible();

    await page.screenshot({
      path: path.join(SCREENSHOT_DIR, 'stop-confirm-dialog.png'),
      fullPage: false,
    });
  });

  test('Visual capture of Deploy Handoff and Card Highlight', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await setupOps(page, { projects: [...buildProjects(), buildHandoffProject()] });

    // Start deploy
    await page.getByRole('button', { name: '새 배포', exact: true }).click();
    await page.getByRole('button', { name: /One Action Deploy/ }).click();
    await expect(page.getByRole('dialog')).toBeHidden();

    // Emit completions
    await emit(page, 'done', { target: 'local', url: 'https://handoff-local.trycloudflare.com' });
    await emit(page, 'done', { target: 'cloudrun', url: 'https://handoff-an.a.run.app' });

    const handoffBtn = page.getByRole('button', { name: '앱 관리에서 보기' });
    await expect(handoffBtn).toBeVisible();

    // Screenshot HUD with handoff button
    await page.screenshot({
      path: path.join(SCREENSHOT_DIR, 'handoff-hud-button.png'),
      fullPage: false,
    });

    // Click handoff button
    await handoffBtn.click();
    await expect(page.getByRole('tab', { name: '앱 관리' })).toHaveAttribute('aria-selected', 'true');
    const highlightedCard = page.getByRole('article', { name: 'handoff-app', exact: true });
    await expect(highlightedCard).toHaveClass(/ops-card-highlight/);

    await page.screenshot({
      path: path.join(SCREENSHOT_DIR, 'handoff-card-highlighted.png'),
      fullPage: false,
    });
  });

  test('Visual capture of Empty State with Call to Action', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await setupOps(page, { projects: [] });
    await openAppsTab(page);

    await expect(page.getByText('아직 배포한 앱이 없어요')).toBeVisible();
    await expect(page.locator('.ops-empty').getByRole('button', { name: '새 배포' })).toBeVisible();

    await page.screenshot({
      path: path.join(SCREENSHOT_DIR, 'empty-state.png'),
      fullPage: false,
    });
  });

  test('Visual capture of No Fleet Connected State', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await setupOps(page, { fleet: null });
    await openAppsTab(page);

    await expect(page.getByText('노드 풀이 연결되지 않았어요')).toBeVisible();

    await page.screenshot({
      path: path.join(SCREENSHOT_DIR, 'no-fleet-connected.png'),
      fullPage: false,
    });
  });

  test('Visual capture of Details Modal Open', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await setupOps(page);
    await openAppsTab(page);

    // Click '새 배포' in modal
    await page.getByRole('button', { name: '새 배포', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();

    await page.screenshot({
      path: path.join(SCREENSHOT_DIR, 'details-modal-deploy.png'),
      fullPage: false,
    });
  });
});
