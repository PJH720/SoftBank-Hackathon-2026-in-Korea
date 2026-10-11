import { test as base, expect } from '@playwright/test';
import { LONG_NAME, RUN_ID, buildHandoffProject, buildProjects, emit, openAppsTab, setupOps } from './fixtures/ops.js';
import { EXPIRED_LOGS } from '../src/lib/operations.js';

// Any console error fails the test, React key warnings included.
const test = base.extend({
  page: async ({ page }, runTest) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error' && !message.text().includes('Error creating WebGL context') && !message.text().startsWith('Failed to load resource:')) errors.push(message.text());
    });
    await runTest(page);
    expect(errors).toEqual([]);
  },
});

const EXPECTED_CARDS = [
  { name: 'guestbook', status: '정상', sentence: '2곳에서 정상 서비스 중이에요', targets: ['Local Docker', 'Cloud Run'], states: ['serving', 'serving'] },
  { name: 'todo-api', status: '확인 필요', sentence: 'Cloud Run 배포는 실패했지만 이전 버전이 계속 서비스 중이에요', targets: ['Local Docker', 'Cloud Run'], states: ['serving', 'serving_previous'] },
  { name: 'shop-node', status: '정상', sentence: 'Node Pool에서 정상 서비스 중이에요', targets: ['Node Pool'], states: ['serving'] },
  { name: 'blog', status: '중지됨', sentence: '중지됐어요 · 다시 배포하면 바로 살아나요', targets: ['Cloud Run'], states: ['stopped'] },
  { name: 'chat-ws', status: '배포 중', sentence: '새 버전을 배포하는 중이에요 · 그동안 이전 버전이 계속 서비스돼요', targets: ['Local Docker'], states: ['deploying'] },
  { name: 'broken-app', status: '실패', sentence: '서비스 중인 곳이 없어요 · 로그를 확인하고 다시 배포해 보세요', targets: ['Cloud Run'], states: ['failed'] },
  { name: LONG_NAME, status: '정상', sentence: 'Cloud Run functions에서 정상 서비스 중이에요', targets: ['Cloud Run functions'], states: ['serving'] },
];

const card = (page, name) => page.getByRole('article', { name, exact: true });
const streamCount = (page) => page.evaluate(() => window.testStreams.length);

async function startDeploy(page) {
  await page.getByRole('button', { name: '새 배포', exact: true }).click();
  await page.getByRole('button', { name: /One Action Deploy/ }).click();
  await expect(page.getByRole('dialog')).toBeHidden();
}

test('every app card shows its status, sentence and where it is served', async ({ page }) => {
  await setupOps(page);
  await openAppsTab(page);
  await expect(page.locator('.ops-board article')).toHaveCount(EXPECTED_CARDS.length);
  for (const expected of EXPECTED_CARDS) {
    const app = card(page, expected.name);
    await expect(app.locator('.ops-card-head')).toContainText(expected.status);
    await expect(app.locator('.ops-sentence')).toHaveText(expected.sentence);
    await expect(app.locator('.ops-target-label')).toHaveText(expected.targets);
    expect(await app.locator('.ops-target').evaluateAll((rows) => rows.map((r) => r.dataset.state))).toEqual(expected.states);
  }

  const todo = card(page, 'todo-api');
  await expect(todo.getByText('이전 버전 서비스 중', { exact: true })).toBeVisible();
  await expect(todo.getByText('이번 배포는 실패 · 이전 버전 유지 중')).toBeVisible();

  // shop-node is 'shop' in the node pool: matched by hostname, not by name or port
  const shop = card(page, 'shop-node');
  await expect(shop.getByText('복제본 2개')).toBeVisible();
  await expect(shop.locator('.ops-replica')).toHaveText([/gcp-seoul.*22%/, /aws-tokyo.*64%/]);

  const chips = page.locator('.ops-chips li');
  await expect(chips).toHaveText(['운영 중 3', '확인 필요 2', '배포 중 1', '중지 1', '노드 2/3 정상']);
  const counts = await chips.locator('strong').allTextContents();
  expect(counts.slice(0, 4).reduce((sum, n) => sum + Number(n), 0)).toBe(EXPECTED_CARDS.length);
});

test('open links point at the served URLs and open safely in a new tab', async ({ page }) => {
  await setupOps(page);
  await openAppsTab(page);
  const guestbook = card(page, 'guestbook');
  await expect(guestbook.getByRole('link', { name: '열기', exact: true })).toHaveAttribute('href', 'https://guestbook-local.trycloudflare.com');
  await expect(guestbook.getByRole('link', { name: 'Local Docker 열기' })).toHaveAttribute('href', 'https://guestbook-local.trycloudflare.com');
  await expect(guestbook.getByRole('link', { name: 'Cloud Run 열기' })).toHaveAttribute('href', 'https://guestbook-abc123-an.a.run.app');
  await expect(card(page, 'shop-node').getByRole('link', { name: '열기', exact: true })).toHaveAttribute('href', 'http://shop.34.64.10.20.nip.io:8080');
  // the previous Cloud Run version has no URL in live, so todo-api only links Local Docker
  await expect(card(page, 'todo-api').getByRole('link')).toHaveCount(2);
  await expect(card(page, 'blog').getByRole('link')).toHaveCount(0);
  await expect(card(page, 'broken-app').getByRole('link')).toHaveCount(0);

  const links = await page.locator('.ops-board a').evaluateAll((all) => all.map((a) => ({ href: a.getAttribute('href'), target: a.target, rel: a.rel })));
  expect(links.length).toBeGreaterThan(0);
  for (const link of links) {
    expect(link.href).toMatch(/^https?:\/\//);
    expect(link.target).toBe('_blank');
    expect(link.rel).toBe('noopener noreferrer');
  }
});

test('로그 보기 attaches one stream when the deployment still exists', async ({ page }) => {
  const sent = await setupOps(page);
  await openAppsTab(page);
  await card(page, 'guestbook').getByRole('button', { name: '로그 보기' }).click();
  await expect(page.getByRole('tab', { name: '배포 여정' })).toHaveAttribute('aria-selected', 'true');
  await expect.poll(() => streamCount(page)).toBe(1);
  expect(await page.evaluate(() => window.testStreams[0].url)).toContain('/deploy/dep-guestbook/events');
  expect(sent.lookups).toContain('/deploy/dep-guestbook');
});

test('로그 보기 explains an expired deployment and opens no stream', async ({ page }) => {
  await setupOps(page, { lookup: 404 });
  await openAppsTab(page);
  await card(page, 'guestbook').getByRole('button', { name: '로그 보기' }).click();
  await expect(page.getByText(EXPIRED_LOGS)).toBeVisible();
  await expect(page.getByRole('tab', { name: '앱 관리' })).toHaveAttribute('aria-selected', 'true');
  expect(await streamCount(page)).toBe(0);
});

for (const [name, expected] of [
  ['guestbook', { source: 'https://github.com/team-gold/guestbook', targets: ['local', 'cloudrun'], ask_env: true, name: 'guestbook', ref: 'main' }],
  ['todo-api', { source: 'sample-apps/todo-api', targets: ['local', 'cloudrun'], ask_env: true, name: 'todo-api' }],
]) {
  test(`다시 배포 sends the same app again (${name})`, async ({ page }) => {
    const sent = await setupOps(page);
    await openAppsTab(page);
    await card(page, name).getByRole('button', { name: '다시 배포' }).click();
    await expect(page.getByRole('tab', { name: '배포 여정' })).toHaveAttribute('aria-selected', 'true');
    expect(sent.deploys).toEqual([expected]);
    await expect.poll(() => streamCount(page)).toBe(1);
    expect(await page.evaluate(() => window.testStreams[0].url)).toContain(`/deploy/${RUN_ID}/events`);
  });
}

test('다시 배포 waits for a running app and for the run being watched', async ({ page }) => {
  const sent = await setupOps(page);
  await openAppsTab(page);
  await expect(card(page, 'chat-ws').getByRole('button', { name: '다시 배포' })).toBeDisabled();
  await expect(card(page, 'guestbook').getByRole('button', { name: '다시 배포' })).toBeEnabled();

  await card(page, 'guestbook').getByRole('button', { name: '로그 보기' }).click();
  await expect(page.getByRole('tab', { name: '배포 여정' })).toHaveAttribute('aria-selected', 'true');
  await openAppsTab(page);
  await expect(card(page, 'guestbook').getByText('보는 중')).toBeVisible();
  const buttons = page.locator('.ops-board').getByRole('button', { name: '다시 배포' });
  await expect(buttons).toHaveCount(EXPECTED_CARDS.length);
  for (const button of await buttons.all()) await expect(button).toBeDisabled();
  expect(sent.deploys).toEqual([]);
});

test('중지 asks first, 취소 sends nothing and 중지 확인 stops exactly once', async ({ page }) => {
  const sent = await setupOps(page);
  await openAppsTab(page);
  const guestbook = card(page, 'guestbook');
  await guestbook.getByRole('button', { name: '중지', exact: true }).click();
  await expect(guestbook.getByText(/모든 서비스를 중지할까요\?.*DB 데이터는 보존돼요/)).toBeVisible();
  await guestbook.getByRole('button', { name: '취소' }).click();
  await expect(guestbook.getByText(/모든 서비스를 중지할까요\?/)).toBeHidden();
  expect(sent.stops).toEqual([]);

  await guestbook.getByRole('button', { name: '중지', exact: true }).click();
  await guestbook.getByRole('button', { name: '중지 확인' }).click();
  await expect(page.getByText('guestbook 중지됨')).toBeVisible();
  expect(sent.stops).toEqual([{ method: 'POST', name: 'guestbook' }]);
});

test('scene tabs rove with arrows, wrap around and jump with Home/End', async ({ page }) => {
  await setupOps(page);
  const tab = (name) => page.getByRole('tab', { name });
  const expectSelected = async (name) => {
    await expect(tab(name)).toHaveAttribute('aria-selected', 'true');
    await expect(tab(name)).toBeFocused();
  };
  await expect(page.getByRole('tab')).toHaveText(['배포 여정', '앱 관리', '노드 풀']);
  await tab('배포 여정').focus();
  await page.keyboard.press('ArrowRight');
  await expectSelected('앱 관리');
  await page.keyboard.press('ArrowRight');
  await expectSelected('노드 풀');
  await page.keyboard.press('ArrowRight');
  await expectSelected('배포 여정');
  await page.keyboard.press('ArrowLeft');
  await expectSelected('노드 풀');
  await page.keyboard.press('Home');
  await expectSelected('배포 여정');
  await page.keyboard.press('End');
  await expectSelected('노드 풀');
  await page.keyboard.press('ArrowLeft');
  await expectSelected('앱 관리');
});

for (const [width, height] of [[1366, 768], [375, 320]]) {
  test(`앱 관리 stays one screen and scrolls inside the board at ${width}×${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await setupOps(page);
    await openAppsTab(page);
    const page_ = await page.evaluate(() => ({ height: document.documentElement.scrollHeight, width: document.documentElement.scrollWidth, viewportHeight: innerHeight, viewportWidth: innerWidth }));
    expect(page_.height).toBeLessThanOrEqual(page_.viewportHeight);
    expect(page_.width).toBeLessThanOrEqual(page_.viewportWidth);

    // the shell clips, so check the board itself for overflow
    const board = page.locator('.ops-board');
    const metrics = await board.evaluate((el) => ({ scrollWidth: el.scrollWidth, clientWidth: el.clientWidth, scrollHeight: el.scrollHeight, clientHeight: el.clientHeight }));
    expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth);
    expect(metrics.scrollHeight).toBeGreaterThan(metrics.clientHeight);
    const boardBox = await board.boundingBox();
    const longBox = await card(page, LONG_NAME).boundingBox();
    expect(longBox.x + longBox.width).toBeLessThanOrEqual(boardBox.x + boardBox.width + 0.5);

    for (const tab of await page.getByRole('tab').all()) {
      await expect(tab).toBeInViewport();
      expect(await tab.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
    }
    await expect(page.getByRole('button', { name: '새 배포', exact: true }).first()).toBeInViewport();
    await expect(page.getByRole('button', { name: '전체 로그 보기' })).toBeInViewport();

    await board.evaluate((el) => { el.scrollTop = el.scrollHeight; });
    expect(await board.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
    expect(await page.evaluate(() => ({ y: scrollY, shell: document.querySelector('.dashboard-shell').scrollTop }))).toEqual({ y: 0, shell: 0 });
    await page.screenshot({ path: test.info().outputPath('apps.png') });
  });
}

test('after a deploy, 앱 관리에서 보기 opens the board on that app', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 667 });
  await setupOps(page, { projects: [...buildProjects(), buildHandoffProject()] });
  await startDeploy(page);
  const handoff = page.getByRole('button', { name: '앱 관리에서 보기' });
  await expect(handoff).toBeHidden();
  await emit(page, 'done', { target: 'local', url: 'https://handoff-local.trycloudflare.com' });
  await emit(page, 'done', { target: 'cloudrun', url: 'https://handoff-an.a.run.app' });
  await expect(handoff).toBeInViewport();
  expect(await page.locator('.run-headline').evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
  await page.screenshot({ path: test.info().outputPath('handoff-hud.png') });

  await handoff.click();
  await expect(page.getByRole('tab', { name: '앱 관리' })).toHaveAttribute('aria-selected', 'true');
  const app = card(page, 'handoff-app');
  await expect(app).toHaveClass(/ops-card-highlight/);
  await expect(app).toBeInViewport();
  // scrollIntoView may only move the board, never the clipped ancestors or the page
  const offsets = await page.evaluate(() => ({
    page: scrollY,
    ...Object.fromEntries(['.scene-view', '.dashboard-stage', '.dashboard-shell'].map((s) => [s, document.querySelector(s).scrollTop])),
  }));
  expect(offsets).toEqual({ page: 0, '.scene-view': 0, '.dashboard-stage': 0, '.dashboard-shell': 0 });
  await expect(app).not.toHaveClass(/ops-card-highlight/, { timeout: 7000 });
});

test('without a node pool the board still renders and says so', async ({ page }) => {
  await setupOps(page, { fleet: null });
  await openAppsTab(page);
  await expect(page.getByText('노드 풀이 연결되지 않았어요')).toBeVisible();
  await expect(page.locator('.ops-board article')).toHaveCount(EXPECTED_CARDS.length);
  await expect(page.locator('.ops-chips li')).toHaveText(['운영 중 3', '확인 필요 2', '배포 중 1', '중지 1']);
  await expect(card(page, 'shop-node').locator('.ops-target-label')).toHaveText(['Node Pool']);
  await expect(card(page, 'shop-node').getByText(/복제본/)).toHaveCount(0);
});

test('with no apps the board offers a first deploy', async ({ page }) => {
  await setupOps(page, { projects: [] });
  await openAppsTab(page);
  const board = page.locator('.ops-board');
  await expect(board.getByText('아직 배포한 앱이 없어요')).toBeVisible();
  await board.getByRole('button', { name: '새 배포' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog.getByPlaceholder('https://github.com/owner/repo')).toBeVisible();
  await expect(dialog.getByRole('navigation').getByRole('button')).toHaveText(['새 배포', '배포 상세', '설정']);
  await expect(dialog.getByRole('button', { name: '새 배포' })).toHaveAttribute('aria-current', 'page');
});
