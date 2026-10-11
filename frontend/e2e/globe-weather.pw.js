import { test as base, expect } from '@playwright/test';
import { buildFleet, emit, setupOps } from './fixtures/ops.js';

// Same console guard as dashboard.pw.js: any runtime error from the new canvases fails the test.
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

async function weatherCanvas(page) {
  const backdrop = page.locator('header.dashboard-header .weather-background');
  const canvas = backdrop.locator('canvas');
  await expect(canvas).toBeAttached();
  return { backdrop, canvas };
}

/** The topmost element at the centre of the button must be the button itself (or inside it). */
async function hitsButton(locator) {
  return locator.evaluate((button) => {
    const box = button.getBoundingClientRect();
    const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    return button === hit || button.contains(hit);
  });
}

test('weather background sits behind the header and never takes clicks', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await setupOps(page);
  const { backdrop, canvas } = await weatherCanvas(page);
  await expect(backdrop).toHaveAttribute('aria-hidden', 'true');
  await expect(backdrop).toHaveCSS('pointer-events', 'none');
  await expect(canvas).toHaveCSS('pointer-events', 'none');
  // R3F's own wrapper div sets pointer-events inline, so check it too
  await expect(canvas.locator('xpath=..')).toHaveCSS('pointer-events', 'none');

  // fills the header's padding box exactly (the bounding box would add the 1.5px bottom border)
  const fit = await backdrop.evaluate((el) => {
    const header = el.parentElement;
    return { width: el.offsetWidth, height: el.offsetHeight, headerWidth: header.clientWidth, headerHeight: header.clientHeight };
  });
  expect(fit.width).toBe(fit.headerWidth);
  expect(fit.height).toBe(fit.headerHeight);

  const dialog = page.getByRole('dialog');
  for (const name of ['새 배포', '배포 상세']) {
    const button = page.getByRole('button', { name, exact: true });
    expect(await hitsButton(button)).toBe(true);
    await button.click();
    await expect(dialog).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
  }
});

for (const [width, height] of [[1440, 900], [375, 667]]) {
  test(`header background keeps one screen at ${width}×${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await setupOps(page);
    await weatherCanvas(page);
    const size = await page.evaluate(() => ({ height: document.documentElement.scrollHeight, width: document.documentElement.scrollWidth, viewportHeight: innerHeight, viewportWidth: innerWidth }));
    expect(size.height).toBe(size.viewportHeight);
    expect(size.width).toBeLessThanOrEqual(size.viewportWidth);
    expect(await hitsButton(page.getByRole('button', { name: '새 배포', exact: true }))).toBe(true);
  });
}

test('node pool toggles between the grid and the globe', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await setupOps(page);
  await page.getByRole('tab', { name: '노드 풀' }).click();

  const toggle = page.getByRole('group', { name: '노드 풀 보기 방식' });
  const gridButton = toggle.getByRole('button', { name: '그리드', exact: true });
  const globeButton = toggle.getByRole('button', { name: '지구본', exact: true });
  const grid = page.getByLabel('노드 3개 3D 상태 그리드');
  await expect(grid).toBeVisible();
  await expect(gridButton).toHaveAttribute('aria-pressed', 'true');
  await expect(globeButton).toHaveAttribute('aria-pressed', 'false');

  await globeButton.focus();
  await page.keyboard.press('Enter');
  await expect(globeButton).toHaveAttribute('aria-pressed', 'true');
  const globe = page.getByLabel(/글로벌 노드 풀 3D 지구본/);
  await expect(globe).toBeVisible();
  await expect(globe).toHaveAttribute('aria-label', '글로벌 노드 풀 3D 지구본, 리전 4곳 중 2곳 연결');
  await expect(page.locator('#scene-view canvas')).toBeVisible();
  await expect(grid).toBeHidden();

  // fixture: gcp-seoul (ok, load 0.6 / 2 cpus), aws-tokyo (overloaded), oci-osaka (no slot)
  const legend = page.locator('ul[aria-label="리전별 노드 상태"]');
  await expect(legend.getByRole('button')).toHaveCount(5);
  await expect(legend.getByRole('button', { name: /서울.*CPU 부하 30%/ })).toBeVisible();
  await expect(legend.getByRole('button', { name: /도쿄.*CPU 부하 95%/ })).toBeVisible();
  await expect(legend.getByRole('button', { name: /싱가포르.*데이터 없음/ })).toBeVisible();
  await expect(legend.getByRole('button', { name: /버지니아.*데이터 없음/ })).toBeVisible();
  await expect(legend.getByRole('button', { name: /로컬 핑.*백엔드 연결됨/ })).toBeVisible();
  await expect(page.getByText('+1 미배치 노드')).toBeVisible();

  const seoul = legend.getByRole('button', { name: /서울/ });
  await seoul.click();
  await expect(seoul).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: '전체 보기' }).click();
  await expect(seoul).toHaveAttribute('aria-pressed', 'false');

  await gridButton.click();
  await expect(grid).toBeVisible();
  await expect(globe).toBeHidden();
  await expect(gridButton).toHaveAttribute('aria-pressed', 'true');
});

test('globe view with no fleet shows no invented load', async ({ page }) => {
  await setupOps(page, { fleet: null });
  await page.getByRole('tab', { name: '노드 풀' }).click();
  await expect(page.getByText('노드 풀 데이터를 불러오지 못했습니다.', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: '지구본', exact: true }).click();
  const legend = page.locator('ul[aria-label="리전별 노드 상태"]');
  for (const region of ['서울', '도쿄', '싱가포르', '버지니아']) {
    await expect(legend.getByRole('button', { name: new RegExp(`${region}.*데이터 없음`) })).toBeVisible();
  }
  await expect(legend.getByText(/CPU 부하 \d/)).toHaveCount(0);
});

// Inspect the mounted scene via R3F's exported roots, without adding production test hooks.
// The transformed Vite import includes the same version key as the component's dependency.
async function sceneSnapshot(page, kind = 'globe') {
  return page.evaluate(async (kind) => {
    const weather = kind === 'weather';
    const source = await (await fetch(`/src/components/3d/${weather ? 'CloudWeatherBackground' : 'GlobalFleetGlobe'}.jsx`)).text();
    const moduleUrl = source.match(/from "([^"]*deps\/@react-three_fiber.js[^"]*)"/)[1];
    const { _roots } = await import(moduleUrl);
    const canvas = document.querySelector(weather ? 'header .weather-background canvas' : '#scene-view canvas');
    const store = _roots.get(canvas)?.store;
    if (!store) return { camera: [], particles: [], seoul: null };
    const { scene, camera } = store.getState();
    const particles = [];
    let seoul;
    scene.traverse((object) => {
      if (object.isPoints) {
        particles.push({ speed: [...object.geometry.attributes.speed.array], opacity: [...object.geometry.attributes.opacity.array] });
      }
      // The Seoul pin's parent is placed at latitude 37.5665 on the unit sphere.
      if (object.isMesh && object.geometry.parameters?.radius === 0.034 && Math.abs(object.parent.position.y - Math.sin(37.5665 * Math.PI / 180)) < 0.0001) {
        const world = object.getWorldPosition(camera.position.clone()).project(camera);
        const box = canvas.getBoundingClientRect();
        seoul = { x: box.left + (world.x + 1) * box.width / 2, y: box.top + (1 - world.y) * box.height / 2 };
      }
    });
    return { camera: camera.position.toArray(), particles, seoul };
  }, kind);
}

test('a 3D pin click focuses the camera and mobile full view stays usable', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await setupOps(page);
  await page.getByRole('tab', { name: '노드 풀' }).click();
  await page.getByRole('button', { name: '지구본', exact: true }).click();
  await expect(page.locator('#scene-view canvas')).toBeVisible();
  await expect.poll(async () => Boolean((await sceneSnapshot(page)).seoul)).toBe(true);
  const before = await sceneSnapshot(page);
  await page.mouse.click(before.seoul.x, before.seoul.y);
  const seoul = page.locator('ul[aria-label="리전별 노드 상태"]').getByRole('button', { name: /서울/ });
  await expect(seoul).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(async () => {
    const next = (await sceneSnapshot(page)).camera;
    return Math.hypot(...next.map((v, i) => v - before.camera[i]));
  }).toBeGreaterThan(0.5);
  await expect(page.getByText('gcp-seoul', { exact: true })).toBeVisible();
  await expect(page.getByText('1분 load / core', { exact: true })).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('globe-focused.png') });

  await page.setViewportSize({ width: 375, height: 667 });
  const reset = page.getByRole('button', { name: '전체 보기', exact: true });
  await expect(reset).toBeInViewport();
  expect(await hitsButton(reset)).toBe(true);
  await reset.click();
  await expect(seoul).toHaveAttribute('aria-pressed', 'false');
  await page.screenshot({ path: test.info().outputPath('globe-mobile.png') });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
});

test('fleet polling updates a focused tooltip without resetting selection', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await setupOps(page);
  await page.getByRole('tab', { name: '노드 풀' }).click();
  await page.getByRole('button', { name: '지구본', exact: true }).click();
  const seoul = page.locator('ul[aria-label="리전별 노드 상태"]').getByRole('button', { name: /서울/ });
  await seoul.click();
  await expect(page.getByText('30%', { exact: true })).toBeVisible();
  const fleet = buildFleet();
  fleet.nodes[0] = { ...fleet.nodes[0], load1: 1.8, mem_avail_mb: 409.6 };
  await page.route((url) => url.pathname === '/fleet', (route) => route.fulfill({ json: fleet }));
  await expect(seoul).toHaveText(/과부하.*CPU 부하 90%/, { timeout: 18000 });
  await expect(seoul).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByText('90%', { exact: true })).toHaveCount(2);
});

test('weather parallax follows window movement and recentres when reduced motion changes', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await setupOps(page);
  await weatherCanvas(page);
  await page.mouse.move(1420, 20);
  await expect.poll(async () => (await sceneSnapshot(page, 'weather')).camera[0]).toBeGreaterThan(0.3);
  await expect.poll(async () => (await sceneSnapshot(page, 'weather')).camera[1]).toBeGreaterThan(0.3);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect.poll(async () => {
    const [x, y] = (await sceneSnapshot(page, 'weather')).camera;
    return Math.hypot(x, y);
  }).toBeLessThan(0.03);
  expect(await hitsButton(page.getByRole('button', { name: '새 배포', exact: true }))).toBe(true);
});

test('globe GPU initialization failure preserves the deployment form and view toggle', async ({ page }) => {
  await setupOps(page);
  await page.evaluate(() => {
    const getContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (type, ...args) {
      return type.startsWith('webgl') ? null : getContext.call(this, type, ...args);
    };
  });
  await page.getByRole('tab', { name: '노드 풀' }).click();
  await page.getByRole('button', { name: '지구본', exact: true }).click();
  await expect(page.getByText('3D 화면을 사용할 수 없습니다.', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: '그리드', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '새 배포', exact: true }).click();
  const source = page.getByRole('dialog').getByPlaceholder('https://github.com/owner/repo');
  await source.fill('sample-apps/guestbook');
  await expect(source).toHaveValue('sample-apps/guestbook');
});

for (const outcome of ['completed', 'partial_failure', 'cancelled']) {
  test(`weather follows running, healing and ${outcome} without replacing its canvas`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await setupOps(page);
    const { backdrop, canvas } = await weatherCanvas(page);
    const tint = backdrop.locator(':scope > div').first();
    await expect(tint).toHaveCSS('background-color', 'rgba(56, 189, 248, 0.04)');
    const initialCanvas = await canvas.elementHandle();
    await page.getByRole('button', { name: '새 배포', exact: true }).click();
    const source = page.getByRole('dialog').getByPlaceholder('https://github.com/owner/repo');
    await source.fill('sample-apps/guestbook');
    await page.getByRole('button', { name: /One Action Deploy/ }).click();
    await expect(page.getByRole('dialog')).toBeHidden();
    await expect(tint).toHaveCSS('background-color', 'rgba(99, 102, 241, 0.1)');
    await emit(page, 'stage', { target: 'local' }, 'deploy');
    await emit(page, 'stage', { target: 'local', stderr: '[deployer] runtime error' }, 'heal');
    await expect(tint).toHaveCSS('background-color', 'rgba(16, 185, 129, 0.08)');
    await expect.poll(async () => {
      const { particles } = await sceneSnapshot(page, 'weather');
      return particles.length > 0 && particles.every((p) => p.speed.every((speed) => speed === 0));
    }).toBe(true);

    if (outcome === 'cancelled') {
      await emit(page, 'error', { cancelled: true });
      await expect(tint).toHaveCSS('background-color', 'rgba(56, 189, 248, 0.04)');
    } else {
      await emit(page, 'done', { target: 'local', url: 'https://local.example' });
      if (outcome === 'completed') {
        await emit(page, 'done', { target: 'cloudrun', url: 'https://cloud.example' });
        await expect(tint).toHaveCSS('background-color', 'rgba(250, 204, 21, 0.05)');
      } else {
        await emit(page, 'error', { target: 'cloudrun', message: 'cloud unavailable' });
        await expect(tint).toHaveCSS('background-color', 'rgba(30, 41, 59, 0.35)');
      }
    }
    expect(await canvas.evaluate((el, original) => el === original, initialCanvas)).toBe(true);
    expect(await hitsButton(page.getByRole('button', { name: '새 배포', exact: true }))).toBe(true);
    await page.screenshot({ path: test.info().outputPath(`weather-${outcome}.png`) });
  });
}
