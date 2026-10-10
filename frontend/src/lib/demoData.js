// Demo data and scenario presets for visual verification by the user without requiring a live cloud backend.

export const LONG_NAME = 'very-long-application-name-for-layout-ok';
export const DEMO_RUN_ID = 'demo-run-handoff';

export function buildDemoProjects(now = Date.now() / 1000) {
  return [
    {
      name: 'guestbook',
      source: 'https://github.com/team-gold/guestbook',
      ref: 'main',
      targets: ['local', 'cloudrun'],
      last_deployment_id: 'dep-guestbook',
      last_status: 'completed',
      trigger: 'github-push',
      updated_at: now - 180,
      history: ['d1', 'd2', 'd3', 'd4', 'dep-guestbook'],
      urls: { local: 'https://guestbook-local.trycloudflare.com', cloudrun: 'https://guestbook-abc123-an.a.run.app' },
      live: { local: { container: 'guestbook' }, cloudrun: { service: 'guestbook' } },
    },
    {
      name: 'todo-api',
      source: 'sample-apps/todo-api',
      ref: null,
      targets: ['local', 'cloudrun'],
      last_deployment_id: 'dep-todo',
      last_status: 'partial_failure',
      trigger: 'api',
      updated_at: now - 900,
      history: ['t1', 't2', 'dep-todo'],
      urls: { local: 'https://todo-local.trycloudflare.com', cloudrun: null },
      live: { local: { container: 'todo-api' }, cloudrun: { service: 'todo-api' } },
    },
    {
      name: 'shop-node',
      source: 'sample-apps/shop',
      ref: null,
      targets: ['node'],
      last_deployment_id: 'dep-shop',
      last_status: 'completed',
      trigger: 'api',
      updated_at: now - 3600,
      history: ['dep-shop'],
      urls: { node: 'http://shop.34.64.10.20.nip.io:8080' },
      live: { node: { container_name: 'shop-node-1', public_url: 'http://shop.34.64.10.20.nip.io:8080' } },
    },
    {
      name: 'blog',
      source: 'sample-apps/blog',
      ref: null,
      targets: ['cloudrun'],
      last_deployment_id: 'dep-blog',
      last_status: 'stopped',
      trigger: 'api',
      updated_at: now - 7200,
      history: ['b1', 'dep-blog'],
      urls: {},
      live: {},
    },
    {
      name: 'chat-ws',
      source: 'sample-apps/chat',
      ref: 'feature/ws',
      targets: ['local'],
      last_deployment_id: 'dep-chat',
      last_status: 'running',
      trigger: 'github-push',
      updated_at: now - 30,
      history: ['c1', 'dep-chat'],
      urls: { local: 'https://chat-old.trycloudflare.com' },
      live: { local: { container: 'chat-ws' } },
    },
    {
      name: 'broken-app',
      source: 'sample-apps/broken',
      ref: null,
      targets: ['cloudrun'],
      last_deployment_id: 'dep-broken',
      last_status: 'failed',
      trigger: 'api',
      updated_at: now - 600,
      history: ['dep-broken'],
      urls: { cloudrun: null },
      live: {},
    },
    {
      name: LONG_NAME,
      source: 'https://github.com/team-gold/a-really-long-repository-name-that-keeps-going',
      ref: 'main',
      targets: ['function'],
      last_deployment_id: 'dep-long',
      last_status: 'completed',
      trigger: 'github-push',
      updated_at: now - 86400,
      history: ['dep-long'],
      urls: { function: `https://asia-northeast3-proj.cloudfunctions.net/${LONG_NAME}` },
      live: { function: { function: LONG_NAME } },
    },
  ];
}

export function buildDemoFleet(now = Date.now() / 1000) {
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

export function buildDemoHandoffProject(now = Date.now() / 1000) {
  return {
    name: 'handoff-app',
    source: 'sample-apps/guestbook',
    ref: 'main',
    targets: ['local', 'cloudrun'],
    last_deployment_id: DEMO_RUN_ID,
    last_status: 'completed',
    trigger: 'api',
    updated_at: now - 5,
    history: [DEMO_RUN_ID],
    urls: { local: 'https://handoff-local.trycloudflare.com', cloudrun: 'https://handoff-an.a.run.app' },
    live: { local: { container: 'handoff-app' }, cloudrun: { service: 'handoff-app' } },
  };
}
