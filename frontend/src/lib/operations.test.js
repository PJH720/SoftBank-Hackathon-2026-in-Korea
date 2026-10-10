import { describe, it, expect } from 'vitest'
import {
  TARGET_STATE,
  appStatus,
  appTargets,
  canOpenLogs,
  canRedeploy,
  deployMeta,
  fleetAppFor,
  primaryUrl,
  redeployRequest,
  replicaRows,
  sortApps,
  summarizeApps,
} from './operations'
import { TONE } from './style'

const LONG_NAME = 'very-long-application-name-for-layout-ok'

// Fresh objects on every call so no test can leak mutations into another.
function makeFixtures() {
  const now = Date.now() / 1000
  const projects = [
    { name: 'guestbook', source: 'https://github.com/team-gold/guestbook', ref: 'main', targets: ['local', 'cloudrun'], last_deployment_id: 'dep-guestbook', last_status: 'completed', trigger: 'github-push', updated_at: now - 180, history: ['d1', 'd2', 'd3', 'd4', 'dep-guestbook'], urls: { local: 'https://guestbook-local.trycloudflare.com', cloudrun: 'https://guestbook-abc123-an.a.run.app' }, live: { local: { container: 'guestbook' }, cloudrun: { service: 'guestbook' } } },
    { name: 'todo-api', source: 'sample-apps/todo-api', ref: null, targets: ['local', 'cloudrun'], last_deployment_id: 'dep-todo', last_status: 'partial_failure', trigger: 'api', updated_at: now - 900, history: ['t1', 't2', 'dep-todo'], urls: { local: 'https://todo-local.trycloudflare.com', cloudrun: null }, live: { local: { container: 'todo-api' }, cloudrun: { service: 'todo-api' } } },
    { name: 'shop-node', source: 'sample-apps/shop', ref: null, targets: ['node'], last_deployment_id: 'dep-shop', last_status: 'completed', trigger: 'api', updated_at: now - 3600, history: ['dep-shop'], urls: { node: 'http://shop.34.64.10.20.nip.io:8080' }, live: { node: { container_name: 'shop-node-1', public_url: 'http://shop.34.64.10.20.nip.io:8080' } } },
    { name: 'blog', source: 'sample-apps/blog', ref: null, targets: ['cloudrun'], last_deployment_id: 'dep-blog', last_status: 'stopped', trigger: 'api', updated_at: now - 7200, history: ['b1', 'dep-blog'], urls: {}, live: {} },
    { name: 'chat-ws', source: 'sample-apps/chat', ref: 'feature/ws', targets: ['local'], last_deployment_id: 'dep-chat', last_status: 'running', trigger: 'github-push', updated_at: now - 30, history: ['c1', 'dep-chat'], urls: { local: 'https://chat-old.trycloudflare.com' }, live: { local: { container: 'chat-ws' } } },
    { name: 'broken-app', source: 'sample-apps/broken', ref: null, targets: ['cloudrun'], last_deployment_id: 'dep-broken', last_status: 'failed', trigger: 'api', updated_at: now - 600, history: ['dep-broken'], urls: { cloudrun: null }, live: {} },
    { name: LONG_NAME, source: 'https://github.com/team-gold/a-really-long-repository-name-that-keeps-going', ref: 'main', targets: ['function'], last_deployment_id: 'dep-long', last_status: 'completed', trigger: 'github-push', updated_at: now - 86400, history: ['dep-long'], urls: { function: `https://asia-northeast3-proj.cloudfunctions.net/${LONG_NAME}` }, live: { function: { function: LONG_NAME } } },
  ]
  const fleet = {
    router: { host: '34.64.10.20', public_port: 8080 },
    nodes: [
      { node: 'gcp-seoul', ok: true, load1: 0.6, cpus: 2, mem_total_mb: 4096, mem_avail_mb: 2048, containers: 3, score: 0.42, provider: 'gcp', arch: 'amd64', host: '34.64.10.21' },
      { node: 'aws-tokyo', ok: true, load1: 1.9, cpus: 2, mem_total_mb: 2048, mem_avail_mb: 300, containers: 2, score: 0.91, provider: 'aws', arch: 'arm64', host: '13.112.0.5' },
      { node: 'oci-osaka', ok: false, error: 'ssh timeout', provider: 'oci', arch: 'arm64', host: '141.147.0.9' },
    ],
    apps: [
      {
        name: 'shop',
        url: 'http://shop.34.64.10.20.nip.io',
        image: 'cloudmorph/shop:abc',
        replicas: [
          { node: 'gcp-seoul', upstream: '34.64.10.21:31001', cpu: 22.4 },
          { node: 'aws-tokyo', upstream: '13.112.0.5:31002', cpu: 63.7 },
        ],
        events: [
          { ts: now - 3500, type: 'deploy', node: 'gcp-seoul' },
          { ts: now - 1200, type: 'scale_out', node: 'aws-tokyo', reason: 'replica cpu 63% >= 50% x2' },
        ],
      },
    ],
    watch: [],
  }
  return { now, projects, fleet }
}

const byName = (projects, name) => projects.find((p) => p.name === name)
const states = (rows) => Object.fromEntries(rows.map((r) => [r.target, r.state]))

describe('TARGET_STATE', () => {
  it('uses only tones the shared TONE map can render', () => {
    // Arrange
    const tones = Object.values(TARGET_STATE).map((s) => s.tone)
    // Act
    const missing = tones.filter((t) => !TONE[t])
    // Assert
    expect(missing).toEqual([])
    expect(Object.keys(TARGET_STATE).sort()).toEqual(['cancelled', 'deploying', 'failed', 'idle', 'serving', 'serving_previous', 'stopped'])
  })
})

describe('appTargets', () => {
  it('marks every target serving when the last deployment succeeded everywhere', () => {
    // Arrange
    const { projects } = makeFixtures()
    // Act
    const rows = appTargets(byName(projects, 'guestbook'))
    // Assert
    expect(rows).toEqual([
      { target: 'local', label: 'Local Docker', tone: 'emerald', state: 'serving', url: 'https://guestbook-local.trycloudflare.com', servingPrevious: false },
      { target: 'cloudrun', label: 'Cloud Run', tone: 'emerald', state: 'serving', url: 'https://guestbook-abc123-an.a.run.app', servingPrevious: false },
    ])
  })

  it('reports serving_previous when a target failed this time but still has a live handle', () => {
    // Arrange
    const { projects } = makeFixtures()
    // Act
    const rows = appTargets(byName(projects, 'todo-api'))
    // Assert
    expect(states(rows)).toEqual({ local: 'serving', cloudrun: 'serving_previous' })
    expect(rows[1]).toMatchObject({ servingPrevious: true, url: null, tone: 'amber' })
  })

  it('treats a failed node redeploy as serving_previous even though its live handle carries a url', () => {
    // Arrange
    const project = { name: 'shop-node', targets: ['node'], last_status: 'failed', urls: { node: null }, live: { node: { public_url: 'http://shop.1.2.3.4.nip.io:8080' } } }
    // Act
    const [row] = appTargets(project)
    // Assert
    expect(row).toMatchObject({ state: 'serving_previous', servingPrevious: true, url: 'http://shop.1.2.3.4.nip.io:8080' })
  })

  it('shows listed targets as deploying while running and keeps the previous url', () => {
    // Arrange
    const { projects } = makeFixtures()
    // Act
    const rows = appTargets(byName(projects, 'chat-ws'))
    // Assert
    expect(rows).toEqual([
      { target: 'local', label: 'Local Docker', tone: 'violet', state: 'deploying', url: 'https://chat-old.trycloudflare.com', servingPrevious: true },
    ])
  })

  it('keeps extra live targets serving while a deployment of other targets runs', () => {
    // Arrange
    const project = { name: 'a', targets: ['cloudrun'], last_status: 'running', urls: {}, live: { local: { url: 'https://old.trycloudflare.com' } } }
    // Act
    const rows = appTargets(project)
    // Assert
    expect(rows.map((r) => [r.target, r.state, r.servingPrevious, r.url])).toEqual([
      ['cloudrun', 'deploying', false, null],
      ['local', 'serving', false, 'https://old.trycloudflare.com'],
    ])
  })

  it('orders project.targets first and then extra live keys, without duplicates', () => {
    // Arrange
    const project = { name: 'a', targets: ['cloudrun', 'local'], last_status: 'completed', urls: { cloudrun: 'https://a.run.app', local: 'https://a.trycloudflare.com' }, live: { node: { public_url: 'http://a.1.2.3.4.nip.io' }, local: {}, cloudrun: {} } }
    // Act
    const rows = appTargets(project)
    // Assert
    expect(rows.map((r) => r.target)).toEqual(['cloudrun', 'local', 'node'])
    expect(rows[2]).toMatchObject({ state: 'serving', url: 'http://a.1.2.3.4.nip.io' })
  })

  it('serves a legacy record without live from its urls', () => {
    // Arrange
    const project = { name: 'old', targets: ['local', 'cloudrun'], last_status: 'partial_failure', urls: { local: 'https://old.trycloudflare.com', cloudrun: null } }
    // Act
    const rows = appTargets(project)
    // Assert
    expect(states(rows)).toEqual({ local: 'serving', cloudrun: 'failed' })
    expect(rows[0].url).toBe('https://old.trycloudflare.com')
  })

  it('marks a url without a live entry as stopped when the record tracks live', () => {
    // Arrange
    const project = { name: 'a', targets: ['local'], last_status: 'completed', urls: { local: 'https://a.trycloudflare.com' }, live: {} }
    // Act
    const [row] = appTargets(project)
    // Assert
    expect(row).toMatchObject({ state: 'stopped', url: null })
  })

  it('marks every target stopped after a full stop', () => {
    // Arrange
    const { projects } = makeFixtures()
    // Act
    const rows = appTargets(byName(projects, 'blog'))
    // Assert
    expect(rows).toEqual([{ target: 'cloudrun', label: 'Cloud Run', tone: 'slate', state: 'stopped', url: null, servingPrevious: false }])
  })

  it('splits a partial stop into stopped targets and the ones that failed to stop', () => {
    // Arrange: backend deletes stopped targets from both maps and leaves last_status alone
    const project = { name: 'a', targets: ['local', 'cloudrun'], last_status: 'completed', urls: { cloudrun: 'https://a.run.app' }, live: { cloudrun: { service: 'a' } } }
    // Act
    const rows = appTargets(project)
    // Assert
    expect(states(rows)).toEqual({ local: 'stopped', cloudrun: 'serving' })
  })

  it('marks unfinished targets of a cancelled deployment as cancelled', () => {
    // Arrange
    const project = { name: 'a', targets: ['local', 'cloudrun'], last_status: 'cancelled', urls: { local: 'https://a.trycloudflare.com', cloudrun: null }, live: { local: { container: 'a' } } }
    // Act
    const rows = appTargets(project)
    // Assert
    expect(states(rows)).toEqual({ local: 'serving', cloudrun: 'cancelled' })
  })

  it('preserves the previous version and explains cancellation without claiming a deployment failure', () => {
    const project = { name: 'a', targets: ['node'], last_status: 'cancelled', urls: { node: null }, live: { node: { public_url: 'https://a.example.com' } } }
    const [target] = appTargets(project)
    expect(target).toMatchObject({ state: 'serving_previous', url: 'https://a.example.com', note: '이번 배포는 중단 · 이전 버전 유지 중' })
    expect(appStatus(project)).toMatchObject({ key: 'attention', sentence: '배포를 중단했어요 · Node Pool에서는 이전 버전이 계속 서비스 중이에요' })
  })

  it('explains partially completed cancellation as an interrupted deployment instead of stopped services', () => {
    const project = { name: 'a', targets: ['local', 'cloudrun'], last_status: 'cancelled', urls: { local: 'https://a.example.com', cloudrun: null }, live: { local: {} } }
    expect(appStatus(project)).toMatchObject({ key: 'attention', sentence: '배포를 중단했어요 · Local Docker에서는 계속 서비스 중이에요' })
  })

  it('retains failed-deployment explanation for a previous version after failure', () => {
    const project = { name: 'a', targets: ['node'], last_status: 'failed', urls: { node: null }, live: { node: { public_url: 'https://a.example.com' } } }
    expect(appTargets(project)[0].note).toBe('이번 배포는 실패 · 이전 버전 유지 중')
  })

  it('marks a target failed when this deployment wrote null for it and nothing is live', () => {
    // Arrange
    const { projects } = makeFixtures()
    // Act
    const rows = appTargets(byName(projects, 'broken-app'))
    // Assert
    expect(rows).toEqual([{ target: 'cloudrun', label: 'Cloud Run', tone: 'rose', state: 'failed', url: null, servingPrevious: false }])
  })

  it('labels unknown target keys with the raw key', () => {
    // Arrange
    const project = { name: 'a', targets: ['edge'], last_status: 'completed', urls: { edge: 'https://a.example.com' }, live: { edge: {} } }
    // Act
    const [row] = appTargets(project)
    // Assert
    expect(row).toMatchObject({ target: 'edge', label: 'edge', state: 'serving', url: 'https://a.example.com' })
  })

  it('rejects javascript: and data: urls but keeps the successful state', () => {
    // Arrange
    const project = { name: 'a', targets: ['local', 'cloudrun'], last_status: 'completed', urls: { local: 'javascript:alert(1)', cloudrun: 'data:text/html,hi' }, live: { local: {}, cloudrun: {} } }
    // Act
    const rows = appTargets(project)
    // Assert
    expect(rows.map((r) => [r.state, r.url])).toEqual([['serving', null], ['serving', null]])
    expect(primaryUrl(rows)).toBeNull()
  })

  it.each([
    ['null', null],
    ['undefined', undefined],
    ['a string', 'guestbook'],
    ['an empty object', {}],
    ['malformed fields', { name: 'a', targets: 'local', urls: 'x', live: null, last_status: 42 }],
    ['live as an array', { name: 'a', targets: ['local'], live: ['local'], urls: null }],
  ])('never throws for %s', (_label, project) => {
    // Arrange / Act
    const run = () => appStatus(project, appTargets(project))
    // Assert
    expect(run).not.toThrow()
  })
})

describe('appStatus', () => {
  it.each([
    ['guestbook', 'live', '2곳에서 정상 서비스 중이에요', false],
    ['todo-api', 'attention', 'Cloud Run 배포는 실패했지만 이전 버전이 계속 서비스 중이에요', true],
    ['shop-node', 'live', 'Node Pool에서 정상 서비스 중이에요', false],
    ['blog', 'stopped', '중지됐어요 · 다시 배포하면 바로 살아나요', false],
    ['chat-ws', 'deploying', '새 버전을 배포하는 중이에요 · 그동안 이전 버전이 계속 서비스돼요', false],
    ['broken-app', 'failed', '서비스 중인 곳이 없어요 · 로그를 확인하고 다시 배포해 보세요', true],
    [LONG_NAME, 'live', 'Cloud Run functions에서 정상 서비스 중이에요', false],
  ])('derives %s as %s', (name, key, sentence, attention) => {
    // Arrange
    const { projects } = makeFixtures()
    // Act
    const status = appStatus(byName(projects, name))
    // Assert
    expect(status).toMatchObject({ key, sentence, attention })
    expect(TONE[status.tone]).toBeDefined()
    expect(status.label).toBeTruthy()
  })

  it('omits the previous-version note when a first deployment is running', () => {
    // Arrange
    const project = { name: 'a', targets: ['local'], last_status: 'running', history: ['x'] }
    // Act
    const status = appStatus(project)
    // Assert
    expect(status).toMatchObject({ key: 'deploying', sentence: '새 버전을 배포하는 중이에요', tone: 'violet' })
  })

  it('names the failed target and where it still serves when a deploy failed with no previous version', () => {
    // Arrange
    const project = { name: 'a', targets: ['local', 'cloudrun'], last_status: 'partial_failure', urls: { local: 'https://a.trycloudflare.com', cloudrun: null }, live: { local: {} } }
    // Act
    const status = appStatus(project)
    // Assert
    expect(status).toMatchObject({ key: 'attention', tone: 'amber', attention: true })
    expect(status.sentence).toBe('Cloud Run 배포는 실패했어요 · Local Docker에서는 계속 서비스 중이에요')
  })

  it('flags a partial stop for attention and says where it still serves', () => {
    // Arrange
    const project = { name: 'a', targets: ['local', 'cloudrun'], last_status: 'completed', urls: { cloudrun: 'https://a.run.app' }, live: { cloudrun: {} } }
    // Act
    const status = appStatus(project)
    // Assert
    expect(status).toMatchObject({ key: 'attention', sentence: '일부만 중지됐어요 · Cloud Run에서는 계속 서비스 중이에요' })
  })

  it('reports a cancelled deployment that left nothing serving', () => {
    // Arrange
    const project = { name: 'a', targets: ['cloudrun'], last_status: 'cancelled', urls: { cloudrun: null }, live: {} }
    // Act
    const status = appStatus(project)
    // Assert
    expect(status).toMatchObject({ key: 'cancelled', tone: 'slate', attention: false })
    expect(status.sentence).toContain('중단')
  })

  it('accepts precomputed targets', () => {
    // Arrange
    const project = { name: 'a', last_status: 'completed' }
    const targets = [{ target: 'local', label: 'Local Docker', state: 'serving', servingPrevious: false }]
    // Act
    const status = appStatus(project, targets)
    // Assert
    expect(status.key).toBe('live')
  })
})

describe('fleetAppFor', () => {
  it('matches a node url with a port to the fleet app by hostname', () => {
    // Arrange
    const { projects, fleet } = makeFixtures()
    // Act
    const app = fleetAppFor(byName(projects, 'shop-node'), fleet)
    // Assert
    expect(app?.name).toBe('shop')
  })

  it('matches through live.node.public_url when urls has no node entry', () => {
    // Arrange
    const { fleet } = makeFixtures()
    const project = { name: 'x', urls: { node: null }, live: { node: { public_url: 'http://SHOP.34.64.10.20.nip.io:8080/' } } }
    // Act
    const app = fleetAppFor(project, fleet)
    // Assert
    expect(app?.name).toBe('shop')
  })

  it('returns null when nothing matches', () => {
    // Arrange
    const { projects, fleet } = makeFixtures()
    // Act
    const app = fleetAppFor(byName(projects, 'guestbook'), fleet)
    // Assert
    expect(app).toBeNull()
  })

  it('falls back to exact name equality', () => {
    // Arrange
    const { fleet } = makeFixtures()
    const project = { name: 'shop', targets: ['node'], urls: {}, live: {} }
    // Act
    const app = fleetAppFor(project, fleet)
    // Assert
    expect(app?.url).toBe('http://shop.34.64.10.20.nip.io')
  })

  it('returns null for a null fleet or project', () => {
    // Arrange
    const { projects } = makeFixtures()
    // Act / Assert
    expect(fleetAppFor(byName(projects, 'shop-node'), null)).toBeNull()
    expect(fleetAppFor(null, makeFixtures().fleet)).toBeNull()
  })

  it('ignores invalid urls on either side', () => {
    // Arrange
    const fleet = { apps: [{ name: 'other', url: 'not a url' }, { name: 'shop', url: 'http://shop.1.2.3.4.nip.io' }] }
    const project = { name: 'p', urls: { node: '::::' }, live: { node: { public_url: 42 } } }
    // Act
    const app = fleetAppFor(project, fleet)
    // Assert
    expect(app).toBeNull()
  })
})

describe('replicaRows', () => {
  it('rounds cpu and colours it by load', () => {
    // Arrange
    const { fleet } = makeFixtures()
    // Act
    const rows = replicaRows(fleet.apps[0])
    // Assert
    expect(rows).toEqual([
      { node: 'gcp-seoul', upstream: '34.64.10.21:31001', cpu: 22, tone: 'emerald' },
      { node: 'aws-tokyo', upstream: '13.112.0.5:31002', cpu: 64, tone: 'amber' },
    ])
  })

  it('uses the rounded value for thresholds and slate for unknown cpu', () => {
    // Arrange
    const app = { replicas: [{ node: 'a', upstream: 'u', cpu: 49.6 }, { node: 'b', upstream: 'u', cpu: 80 }, { node: 'c', upstream: 'u', cpu: null }, null] }
    // Act
    const rows = replicaRows(app)
    // Assert
    expect(rows.map((r) => [r.cpu, r.tone])).toEqual([[50, 'amber'], [80, 'rose'], [null, 'slate']])
  })

  it('returns an empty list without an app', () => {
    // Arrange / Act / Assert
    expect(replicaRows(null)).toEqual([])
  })
})

describe('summarizeApps', () => {
  it('buckets the fixture set so the counts add up to the total', () => {
    // Arrange
    const { projects, fleet } = makeFixtures()
    // Act
    const summary = summarizeApps(projects, fleet)
    // Assert
    expect(summary).toEqual({ total: 7, live: 3, attention: 2, deploying: 1, stopped: 1, nodesOk: 2, nodesTotal: 3 })
    expect(summary.live + summary.attention + summary.deploying + summary.stopped).toBe(summary.total)
  })

  it('reports zero nodes when the fleet is unavailable', () => {
    // Arrange
    const { projects } = makeFixtures()
    // Act
    const summary = summarizeApps(projects, null)
    // Assert
    expect(summary).toMatchObject({ nodesOk: 0, nodesTotal: 0, total: 7 })
  })

  it('counts cancelled apps as stopped', () => {
    // Arrange
    const projects = [{ name: 'a', targets: ['local'], last_status: 'cancelled', urls: { local: null }, live: {} }]
    // Act
    const summary = summarizeApps(projects, null)
    // Assert
    expect(summary).toMatchObject({ total: 1, stopped: 1 })
  })

  it('handles a missing project list', () => {
    // Arrange / Act / Assert
    expect(summarizeApps(undefined, undefined)).toEqual({ total: 0, live: 0, attention: 0, deploying: 0, stopped: 0, nodesOk: 0, nodesTotal: 0 })
  })
})

describe('sortApps', () => {
  it('returns a new array sorted by name and leaves the input untouched', () => {
    // Arrange
    const { projects } = makeFixtures()
    const before = projects.map((p) => p.name)
    // Act
    const sorted = sortApps(projects)
    // Assert
    expect(sorted).not.toBe(projects)
    expect(projects.map((p) => p.name)).toEqual(before)
    expect(sorted.map((p) => p.name)).toEqual(['blog', 'broken-app', 'chat-ws', 'guestbook', 'shop-node', 'todo-api', LONG_NAME])
  })

  it('keeps the same order when updated_at changes between polls', () => {
    // Arrange
    const { projects } = makeFixtures()
    const repolled = [...projects].reverse().map((p) => ({ ...p, updated_at: p.updated_at + 1 }))
    // Act
    const first = sortApps(projects).map((p) => p.name)
    const second = sortApps(repolled).map((p) => p.name)
    // Assert
    expect(second).toEqual(first)
  })

  it('tolerates missing names and non-arrays', () => {
    // Arrange / Act / Assert
    expect(sortApps(null)).toEqual([])
    expect(sortApps([{ name: 'b' }, {}, null]).map((p) => p?.name)).toEqual([undefined, undefined, 'b'])
  })
})

describe('deployMeta', () => {
  it('describes a push deployment with its ref', () => {
    // Arrange
    const { projects } = makeFixtures()
    const project = byName(projects, 'guestbook')
    // Act
    const meta = deployMeta(project)
    // Assert
    expect(meta).toEqual({ triggerLabel: 'GitHub push · main', count: 5, updatedAt: project.updated_at })
  })

  it('drops the ref when a push has none and labels api deployments as manual', () => {
    // Arrange / Act / Assert
    expect(deployMeta({ trigger: 'github-push', ref: null }).triggerLabel).toBe('GitHub push')
    expect(deployMeta({ trigger: 'api', ref: 'main' }).triggerLabel).toBe('수동 배포')
  })

  it('falls back to zero history and null time for missing fields', () => {
    // Arrange / Act / Assert
    expect(deployMeta(null)).toEqual({ triggerLabel: '수동 배포', count: 0, updatedAt: null })
  })
})

describe('primaryUrl', () => {
  it('returns the first serving url and skips previous-version urls', () => {
    // Arrange
    const targets = [
      { state: 'serving_previous', url: 'https://old.example.com' },
      { state: 'serving', url: null },
      { state: 'serving', url: 'https://new.example.com' },
    ]
    // Act / Assert
    expect(primaryUrl(targets)).toBe('https://new.example.com')
    expect(primaryUrl(undefined)).toBeNull()
  })
})

describe('redeployRequest', () => {
  it('rebuilds the request from the last deployment and keeps the ref', () => {
    // Arrange
    const { projects } = makeFixtures()
    // Act
    const req = redeployRequest(byName(projects, 'guestbook'))
    // Assert
    expect(req).toEqual({ source: 'https://github.com/team-gold/guestbook', targets: ['local', 'cloudrun'], name: 'guestbook', ref: 'main' })
  })

  it('omits a null ref', () => {
    // Arrange
    const { projects } = makeFixtures()
    // Act
    const req = redeployRequest(byName(projects, 'todo-api'))
    // Assert
    expect(req).toEqual({ source: 'sample-apps/todo-api', targets: ['local', 'cloudrun'], name: 'todo-api' })
  })

  it('drops unknown targets and falls back to live keys', () => {
    // Arrange
    const project = { name: 'a', source: 's', ref: '', targets: ['edge'], live: { node: {}, edge: {} } }
    // Act
    const req = redeployRequest(project)
    // Assert
    expect(req).toEqual({ source: 's', targets: ['node'], name: 'a' })
  })

  it.each([
    ['no source', { name: 'a', targets: ['local'] }],
    ['a blank source', { name: 'a', source: '  ', targets: ['local'] }],
    ['no usable targets', { name: 'a', source: 's', targets: ['edge'], live: {} }],
    ['no name', { source: 's', targets: ['local'] }],
    ['null', null],
  ])('returns null for %s', (_label, project) => {
    // Arrange / Act / Assert
    expect(redeployRequest(project)).toBeNull()
  })
})

describe('canRedeploy', () => {
  it('allows a finished app with a usable request', () => {
    // Arrange
    const { projects } = makeFixtures()
    // Act / Assert
    expect(canRedeploy(byName(projects, 'blog'), {})).toEqual({ ok: true, reason: null })
  })

  it.each([
    ['the app itself is deploying', 'chat-ws', {}, '이 앱의 배포가 진행 중이에요'],
    ['the dashboard watches a running deployment', 'blog', { runRunning: true }, '진행 중인 배포가 끝나면 다시 배포할 수 있어요'],
    ['a redeploy request is in flight', 'blog', { redeploying: true }, '다시 배포를 요청하는 중이에요'],
    ['the same app is stopping', 'blog', { stopping: true }, '이 앱을 중지하는 중이에요'],
  ])('blocks when %s', (_label, name, flags, reason) => {
    // Arrange
    const { projects } = makeFixtures()
    // Act
    const result = canRedeploy(byName(projects, name), flags)
    // Assert
    expect(result).toEqual({ ok: false, reason })
  })

  it('blocks when there is no source to redeploy', () => {
    // Arrange
    const project = { name: 'a', targets: ['local'], last_status: 'completed' }
    // Act / Assert
    expect(canRedeploy(project)).toEqual({ ok: false, reason: '다시 배포할 소스 정보가 없어요' })
  })
})

describe('canOpenLogs', () => {
  const project = { name: 'a', last_deployment_id: 'deployment-a' }

  it('allows the active deployment to reuse its stream even while running', () => {
    expect(canOpenLogs(project, { runId: 'deployment-a', runRunning: true })).toEqual({ ok: true, reason: null })
  })

  it('blocks another deployment while the active stream is tracking a running deployment', () => {
    expect(canOpenLogs(project, { runId: 'deployment-b', runRunning: true })).toEqual({ ok: false, reason: '진행 중인 배포가 끝나면 다른 앱의 로그를 볼 수 있어요' })
  })

  it('allows switching after the active deployment finishes', () => {
    expect(canOpenLogs(project, { runId: 'deployment-b', runRunning: false })).toEqual({ ok: true, reason: null })
  })

  it.each([
    [{ launching: true }, '배포를 요청하는 중이에요'],
    [{ lookingUpLogs: true }, '배포 기록을 확인하는 중이에요'],
  ])('blocks conflicting requests with flags %j', (flags, reason) => {
    expect(canOpenLogs(project, flags)).toEqual({ ok: false, reason })
  })

  it.each([null, {}, { last_deployment_id: '' }])('rejects absent deployment ids in %j', (record) => {
    expect(canOpenLogs(record)).toEqual({ ok: false, reason: '조회할 배포 기록이 없어요' })
  })
})
