import { expect } from '@playwright/test';

// GET /projects and GET /fleet shapes from backend/app/main.py. Timestamps are relative to `now` (epoch seconds).
export const LONG_NAME = 'very-long-application-name-for-layout-ok';
export const RUN_ID = 'test-run';

export function buildProjects(now = Date.now() / 1000) {
  return [
    { name: 'guestbook', source: 'https://github.com/team-gold/guestbook', ref: 'main', targets: ['local', 'cloudrun'], last_deployment_id: 'dep-guestbook', last_status: 'completed', trigger: 'github-push', updated_at: now - 180, history: ['d1', 'd2', 'd3', 'd4', 'dep-guestbook'], urls: { local: 'https://guestbook-local.trycloudflare.com', cloudrun: 'https://guestbook-abc123-an.a.run.app' }, live: { local: { container: 'guestbook' }, cloudrun: { service: 'guestbook' } } },
    // cloudrun failed this time but live still holds the previous version
    { name: 'todo-api', source: 'sample-apps/todo-api', ref: null, targets: ['local', 'cloudrun'], last_deployment_id: 'dep-todo', last_status: 'partial_failure', trigger: 'api', updated_at: now - 900, history: ['t1', 't2', 'dep-todo'], urls: { local: 'https://todo-local.trycloudflare.com', cloudrun: null }, live: { local: { container: 'todo-api' }, cloudrun: { service: 'todo-api' } } },
    // fleet app 'shop' matches by hostname even though the names and ports differ
    { name: 'shop-node', source: 'sample-apps/shop', ref: null, targets: ['node'], last_deployment_id: 'dep-shop', last_status: 'completed', trigger: 'api', updated_at: now - 3600, history: ['dep-shop'], urls: { node: 'http://shop.34.64.10.20.nip.io:8080' }, live: { node: { container_name: 'shop-node-1', public_url: 'http://shop.34.64.10.20.nip.io:8080' } } },
    { name: 'blog', source: 'sample-apps/blog', ref: null, targets: ['cloudrun'], last_deployment_id: 'dep-blog', last_status: 'stopped', trigger: 'api', updated_at: now - 7200, history: ['b1', 'dep-blog'], urls: {}, live: {} },
    { name: 'chat-ws', source: 'sample-apps/chat', ref: 'feature/ws', targets: ['local'], last_deployment_id: 'dep-chat', last_status: 'running', trigger: 'github-push', updated_at: now - 30, history: ['c1', 'dep-chat'], urls: { local: 'https://chat-old.trycloudflare.com' }, live: { local: { container: 'chat-ws' } } },
    { name: 'broken-app', source: 'sample-apps/broken', ref: null, targets: ['cloudrun'], last_deployment_id: 'dep-broken', last_status: 'failed', trigger: 'api', updated_at: now - 600, history: ['dep-broken'], urls: { cloudrun: null }, live: {} },
    { name: LONG_NAME, source: 'https://github.com/team-gold/a-really-long-repository-name-that-keeps-going', ref: 'main', targets: ['function'], last_deployment_id: 'dep-long', last_status: 'completed', trigger: 'github-push', updated_at: now - 86400, history: ['dep-long'], urls: { function: `https://asia-northeast3-proj.cloudfunctions.net/${LONG_NAME}` }, live: { function: { function: LONG_NAME } } },
  ];
}

export function buildFleet(now = Date.now() / 1000) {
  return {
    router: { host: '34.64.10.20', public_port: 8080 },
    nodes: [
      { node: 'gcp-seoul', ok: true, load1: 0.6, cpus: 2, mem_total_mb: 4096, mem_avail_mb: 2048, containers: 3, score: 0.42, provider: 'gcp', arch: 'amd64', host: '34.64.10.21' },
      { node: 'aws-tokyo', ok: true, load1: 1.9, cpus: 2, mem_total_mb: 2048, mem_avail_mb: 300, containers: 2, score: 0.91, provider: 'aws', arch: 'arm64', host: '13.112.0.5' },
      { node: 'oci-osaka', ok: false, error: 'ssh timeout', provider: 'oci', arch: 'arm64', host: '141.147.0.9' },
    ],
    apps: [{
      name: 'shop',
      url: 'http://shop.34.64.10.20.nip.io',
      image: 'cloudmorph/shop:abc',
      replicas: [{ node: 'gcp-seoul', upstream: '34.64.10.21:31001', cpu: 22.4 }, { node: 'aws-tokyo', upstream: '13.112.0.5:31002', cpu: 63.7 }],
      events: [{ ts: now - 3500, type: 'deploy', node: 'gcp-seoul' }, { ts: now - 1200, type: 'scale_out', node: 'aws-tokyo', reason: 'replica cpu 63% >= 50% x2' }],
    }],
    watch: [],
  };
}

/** The app a finished RUN_ID deployment leaves behind, so the HUD can resolve currentProject. */
export function buildHandoffProject(now = Date.now() / 1000) {
  return { name: 'handoff-app', source: 'sample-apps/guestbook', ref: null, targets: ['local', 'cloudrun'], last_deployment_id: RUN_ID, last_status: 'completed', trigger: 'api', updated_at: now - 5, history: [RUN_ID], urls: { local: 'https://handoff-local.trycloudflare.com', cloudrun: 'https://handoff-an.a.run.app' }, live: { local: { container: 'handoff-app' }, cloudrun: { service: 'handoff-app' } } };
}

const path = (name) => (url) => url.pathname === name;

/**
 * Route mocks plus the same EventSource stub as dashboard.pw.js. Returns what the page sent.
 * projects: array (default fixture) · fleet: object, or null for a 503 · lookup: status for GET /deploy/<id>.
 */
export async function setupOps(page, { projects, fleet, lookup = 200 } = {}) {
  const now = Date.now() / 1000;
  const projectList = projects ?? buildProjects(now);
  const fleetBody = fleet === undefined ? buildFleet(now) : fleet;
  const sent = { deploys: [], stops: [], lookups: [] };

  await page.addInitScript(() => {
    window.testStreams = [];
    window.EventSource = class {
      constructor(url) { this.url = url; this.handlers = {}; this.closed = false; this.sequence = 0; window.testStreams.push(this); }
      addEventListener(type, fn) { this.handlers[type] = fn; }
      close() { this.closed = true; }
      emit(type, payload, stage) { this.handlers[type]?.({ data: JSON.stringify({ ts: ++this.sequence, payload, stage }) }); }
    };
  });
  // Predicates on the exact pathname, so Vite module requests (/src/...) never hit a mock.
  await page.route(path('/health'), (route) => route.fulfill({ json: { status: 'ok' } }));
  await page.route(path('/projects'), (route) => route.fulfill({ json: { projects: projectList } }));
  await page.route(path('/fleet'), (route) => (fleetBody ? route.fulfill({ json: fleetBody }) : route.fulfill({ status: 503, json: { detail: 'node pool unavailable' } })));
  await page.route(path('/deploy'), (route) => {
    sent.deploys.push(route.request().postDataJSON());
    return route.fulfill({ json: { deployment_id: RUN_ID } });
  });
  await page.route((url) => /^\/deploy\/[^/]+$/.test(url.pathname), (route) => {
    sent.lookups.push(new URL(route.request().url()).pathname);
    const ok = lookup === 200 || new URL(route.request().url()).pathname === `/deploy/${RUN_ID}`;
    return ok ? route.fulfill({ json: { id: 'x', status: 'completed', events: [] } }) : route.fulfill({ status: lookup, json: { detail: 'not found' } });
  });
  await page.route((url) => /^\/projects\/[^/]+\/stop$/.test(url.pathname), (route) => {
    const name = decodeURIComponent(new URL(route.request().url()).pathname.split('/')[2]);
    sent.stops.push({ method: route.request().method(), name });
    return route.fulfill({ json: { name, stopped: ['local', 'cloudrun'], failed: {} } });
  });

  await page.goto('/');
  await expect(page.getByRole('img', { name: /배포 여정: 소스/ })).toBeVisible();
  return sent;
}

export async function openAppsTab(page) {
  const backdrop = page.locator('.drawer-backdrop');
  if (await backdrop.isVisible()) {
    await page.keyboard.press('Escape');
    await expect(backdrop).toBeHidden();
  }
  await page.getByRole('tab', { name: '앱 관리' }).click();
  await expect(page.getByRole('heading', { name: '앱 관리', level: 2 })).toBeVisible();
}

export async function emit(page, type, payload = {}, stage) {
  await page.evaluate(({ type, payload, stage }) => window.testStreams.at(-1).emit(type, payload, stage), { type, payload, stage });
}
