import { test, expect } from '@playwright/test';
import { setupOps, emit, RUN_ID } from './fixtures/ops.js';

const MENU = ['새 배포', '배포 상세', '설정'];

for (const [width, height] of [[1440, 900], [1366, 768], [768, 1024], [375, 667], [375, 320]]) {
  test(`native modal layout and menu navigation at ${width}×${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await setupOps(page);
    await page.getByRole('button', { name: '배포 상세', exact: true }).click();
    const modal = page.getByRole('dialog', { name: '배포 상세', exact: true });
    await expect(modal).toBeVisible();
    expect(await modal.evaluate((el) => el.matches(':modal'))).toBe(true);
    for (const name of MENU) {
      const dialog = page.locator('dialog.details-modal');
      const button = dialog.getByRole('navigation').getByRole('button', { name, exact: true });
      await button.click();
      await expect(dialog).toHaveAccessibleName(name);
      await expect(button).toHaveAttribute('aria-current', 'page');
      await expect(dialog.getByRole('region', { name, exact: true })).toBeVisible();
      await expect(dialog.getByRole('button', { name: '팝업 닫기' })).toBeInViewport();
      // Let the entry animation finish before measuring and capturing the design.
      await expect.poll(async () => dialog.evaluate((el) => el.getAnimations().length)).toBe(0);
      const bounds = await dialog.boundingBox();
      expect(Math.abs(bounds.x + bounds.width / 2 - width / 2)).toBeLessThan(2);
      expect(Math.abs(bounds.y + bounds.height / 2 - height / 2)).toBeLessThan(2);
      expect(bounds.x).toBeGreaterThanOrEqual(11);
      expect(bounds.y).toBeGreaterThanOrEqual(11);
      expect(bounds.width).toBeLessThanOrEqual(880);
      expect(bounds.y + bounds.height).toBeLessThanOrEqual(height - 11);
      const body = dialog.locator('.modal-body');
      expect(await body.evaluate((el) => el.scrollTop)).toBe(0);
      expect(await body.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(1);
      await page.screenshot({ path: test.info().outputPath(`${name}.png`) });
      await body.evaluate((el) => { el.scrollTop = el.scrollHeight; });
      await expect(dialog.getByRole('button', { name: '팝업 닫기' })).toBeInViewport();
      expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBe(height);
    }
    await page.locator('dialog').getByRole('button', { name: '팝업 닫기' }).click();
    await expect(page.getByRole('button', { name: '배포 상세', exact: true })).toBeFocused();
  });
}

test('menu changes preserve drafts and backdrop dismissal requires an outside press', async ({ page }) => {
  await setupOps(page);
  const trigger = page.getByRole('button', { name: '새 배포', exact: true });
  await trigger.click();
  const modal = page.locator('dialog.details-modal');
  const source = modal.getByPlaceholder('https://github.com/owner/repo');
  await source.fill('team/source-draft');
  await modal.getByRole('navigation').getByRole('button', { name: '설정', exact: true }).click();
  await modal.getByRole('navigation').getByRole('button', { name: '새 배포', exact: true }).click();
  await expect(source).toHaveValue('team/source-draft');
  const bounds = await source.boundingBox();
  await page.mouse.move(bounds.x + 10, bounds.y + 10);
  await page.mouse.down();
  await page.mouse.move(3, 3);
  await page.mouse.up();
  await expect(modal).toBeVisible();
  await page.mouse.click(3, 3);
  await expect(modal).toBeHidden();
  await expect(trigger).toBeFocused();
  await trigger.click();
  await expect(source).toHaveValue('team/source-draft');
  await source.click();
  await expect(page.locator('.dashboard-page')).toHaveAttribute('inert', '');
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
});

test('stop confirmation consumes Escape before the deployment modal', async ({ page }) => {
  await setupOps(page, { projects: [{ name: 'guestbook', last_deployment_id: RUN_ID, last_status: 'completed', targets: ['local'], live: { local: { container: 'guestbook' } }, urls: {} }] });
  await page.getByRole('button', { name: '새 배포', exact: true }).click();
  await page.getByRole('button', { name: /One Action Deploy/ }).click();
  await emit(page, 'done', { target: 'local', url: 'https://local.example' });
  await emit(page, 'done', { target: 'cloudrun', url: 'https://cloud.example' });
  await page.getByRole('button', { name: '배포 상세', exact: true }).click();
  const modal = page.getByRole('dialog');
  await modal.getByRole('button', { name: '중지', exact: true }).click();
  await expect(modal.getByRole('button', { name: '중지 확인' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(modal).toBeVisible();
  await expect(modal.getByRole('button', { name: '중지', exact: true })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(modal).toBeHidden();
});

test('modal remains usable inside the fullscreen dashboard', async ({ page }) => {
  await setupOps(page);
  await page.getByRole('button', { name: '전체화면', exact: true }).click();
  await expect.poll(() => page.evaluate(() => document.fullscreenElement?.className)).toBe('dashboard-shell');
  await page.getByRole('button', { name: '배포 상세', exact: true }).click();
  const modal = page.getByRole('dialog');
  await expect(modal).toBeVisible();
  expect(await modal.evaluate((el) => el.matches(':modal') && document.fullscreenElement.contains(el))).toBe(true);
  await modal.getByRole('button', { name: '팝업 닫기' }).click();
  await expect(modal).toBeHidden();
  await expect(page.getByRole('button', { name: '전체화면 종료', exact: true })).toBeVisible();
});

test('native cancel and close events stay synchronized with React', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await setupOps(page);
  const trigger = page.getByRole('button', { name: '배포 상세', exact: true });
  const modal = page.locator('dialog.details-modal');
  await trigger.click();
  expect(await modal.evaluate((el) => getComputedStyle(el).animationName)).toBe('none');
  await modal.evaluate((el) => el.dispatchEvent(new Event('cancel', { cancelable: true })));
  await expect(modal).toBeHidden();
  await expect(trigger).toBeFocused();
  await trigger.click();
  await modal.evaluate((el) => el.close());
  await expect(page.locator('.dashboard-page')).not.toHaveAttribute('inert', '');
  await trigger.click();
  await expect(modal).toBeVisible();
});
