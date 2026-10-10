// Pure derivations for the 앱 관리 board, built only from GET /projects and GET /fleet.
// Backend facts this relies on (backend/app/main.py):
// - a finished deployment writes urls with EVERY target of that job as a key (null where it failed);
// - live merges deployer handles across deployments, so live[t] without urls[t] is the previous version;
// - POST /projects/{name}/stop deletes stopped targets from both live and urls, and sets last_status
//   to 'stopped' only when every target stopped.

import { TARGET_META } from './run'

export const TARGET_STATE = {
  serving: { label: '서비스 중', tone: 'emerald' },
  serving_previous: { label: '이전 버전 서비스 중', tone: 'amber' },
  deploying: { label: '배포 중', tone: 'violet' },
  failed: { label: '실패', tone: 'rose' },
  stopped: { label: '중지됨', tone: 'slate' },
  cancelled: { label: '중단됨', tone: 'slate' },
  idle: { label: '대기', tone: 'slate' },
}

const STATUS_STYLE = {
  live: { label: '정상', tone: 'emerald' },
  attention: { label: '확인 필요', tone: 'amber' },
  deploying: { label: '배포 중', tone: 'violet' },
  failed: { label: '실패', tone: 'rose' },
  stopped: { label: '중지됨', tone: 'slate' },
  cancelled: { label: '중단됨', tone: 'slate' },
}

const CPU_WARN = 50
const CPU_HOT = 80
const SAFE_PROTOCOLS = new Set(['http:', 'https:'])

const isObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v)
const asObject = (v) => (isObject(v) ? v : {})
const hasOwn = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key)

function parseUrl(value) {
  if (typeof value !== 'string' || !value) return null
  try {
    const url = new URL(value)
    return SAFE_PROTOCOLS.has(url.protocol) ? url : null
  } catch {
    return null
  }
}

const safeUrl = (value) => (parseUrl(value) ? value : null)

function handleUrl(handle) {
  const h = asObject(handle)
  return safeUrl(h.public_url) || safeUrl(h.url)
}

function targetKeys(project) {
  const listed = Array.isArray(project.targets) ? project.targets : []
  const keys = [...listed, ...Object.keys(asObject(project.live))]
  return [...new Set(keys.filter((k) => typeof k === 'string' && k))]
}

function row(target, state, url, servingPrevious) {
  return {
    target,
    label: TARGET_META[target]?.label ?? target,
    tone: TARGET_STATE[state].tone,
    state,
    url,
    servingPrevious,
  }
}

// While a deployment runs, urls and live still describe the previous one.
function runningTarget(target, ctx) {
  const handle = ctx.live[target]
  const previousUrl = handle ? safeUrl(ctx.urls[target]) || handleUrl(handle) : null
  if (ctx.listed.has(target)) return row(target, 'deploying', previousUrl, !!handle)
  return row(target, 'serving', previousUrl, false)
}

function servingTarget(target, ctx) {
  const handle = ctx.live[target]
  // Extra live keys come from older deployments that this one did not touch, so they are current.
  if (!ctx.listed.has(target)) return row(target, 'serving', safeUrl(ctx.urls[target]) || handleUrl(handle), false)
  // urls[t] truthy means this deployment succeeded there, even when the url itself is unsafe to link.
  if (ctx.urls[target]) return row(target, 'serving', safeUrl(ctx.urls[target]), false)
  return {
    ...row(target, 'serving_previous', handleUrl(handle), true),
    note: ctx.status === 'cancelled' ? '이번 배포는 중단 · 이전 버전 유지 중' : '이번 배포는 실패 · 이전 버전 유지 중',
  }
}

function idleTarget(target, ctx) {
  const { status, urls, hasLive, hasUrls } = ctx
  if (status === 'stopped') return row(target, 'stopped', null, false)
  if (status === 'cancelled') return row(target, 'cancelled', null, false)
  if (urls[target]) {
    return hasLive ? row(target, 'stopped', null, false) : row(target, 'serving', safeUrl(urls[target]), false)
  }
  if (ctx.listed.has(target)) {
    // A stop deletes the key; a failed deploy leaves it as null.
    const removedByStop = hasLive && hasUrls && !hasOwn(urls, target)
    return row(target, removedByStop ? 'stopped' : 'failed', null, false)
  }
  return row(target, 'idle', null, false)
}

export function appTargets(project) {
  if (!isObject(project)) return []
  const ctx = {
    status: project.last_status,
    listed: new Set(Array.isArray(project.targets) ? project.targets : []),
    live: asObject(project.live),
    urls: asObject(project.urls),
    hasLive: isObject(project.live),
    hasUrls: isObject(project.urls),
  }
  return targetKeys(project).map((target) => {
    if (ctx.status === 'running') return runningTarget(target, ctx)
    if (ctx.live[target]) return servingTarget(target, ctx)
    return idleTarget(target, ctx)
  })
}

const labelsOf = (rows) => rows.map((r) => r.label).join(', ')

function status(key, sentence) {
  return { key, ...STATUS_STYLE[key], sentence, attention: key === 'attention' || key === 'failed' }
}

function attentionSentence(rows, servingRows, deploymentStatus) {
  const previous = rows.filter((r) => r.state === 'serving_previous')
  const failed = rows.filter((r) => r.state === 'failed')
  if (deploymentStatus === 'cancelled') {
    if (previous.length) return `배포를 중단했어요 · ${labelsOf(previous)}에서는 이전 버전이 계속 서비스 중이에요`
    return `배포를 중단했어요 · ${labelsOf(servingRows)}에서는 계속 서비스 중이에요`
  }
  if (failed.length) return `${labelsOf(failed)} 배포는 실패했어요 · ${labelsOf(servingRows)}에서는 계속 서비스 중이에요`
  if (previous.length) return `${labelsOf(previous)} 배포는 실패했지만 이전 버전이 계속 서비스 중이에요`
  return `일부만 중지됐어요 · ${labelsOf(servingRows)}에서는 계속 서비스 중이에요`
}

export function appStatus(project, targets = appTargets(project)) {
  const rows = Array.isArray(targets) ? targets : []
  if (project?.last_status === 'running') {
    return rows.some((r) => r.servingPrevious)
      ? status('deploying', '새 버전을 배포하는 중이에요 · 그동안 이전 버전이 계속 서비스돼요')
      : status('deploying', '새 버전을 배포하는 중이에요')
  }
  const servingRows = rows.filter((r) => r.state === 'serving' || r.state === 'serving_previous')
  if (rows.length && rows.every((r) => r.state === 'serving')) {
    const where = rows.length === 1 ? rows[0].label : `${rows.length}곳`
    return status('live', `${where}에서 정상 서비스 중이에요`)
  }
  if (servingRows.length) return status('attention', attentionSentence(rows, servingRows, project?.last_status))
  const allStopped = rows.length > 0 && rows.every((r) => r.state === 'stopped')
  if (project?.last_status === 'stopped' || allStopped) return status('stopped', '중지됐어요 · 다시 배포하면 바로 살아나요')
  if (project?.last_status === 'cancelled') return status('cancelled', '배포를 중단했어요 · 다시 배포하면 처음부터 올려요')
  return status('failed', '서비스 중인 곳이 없어요 · 로그를 확인하고 다시 배포해 보세요')
}

function nodeHostnames(project) {
  const nodeHandle = asObject(asObject(project.live).node)
  const candidates = [asObject(project.urls).node, nodeHandle.public_url, nodeHandle.url]
  return new Set(candidates.map(parseUrl).filter(Boolean).map((u) => u.hostname))
}

export function fleetAppFor(project, fleet) {
  if (!isObject(project)) return null
  const apps = (Array.isArray(fleet?.apps) ? fleet.apps : []).filter(isObject)
  const hosts = nodeHostnames(project)
  const byHost = hosts.size ? apps.find((app) => hosts.has(parseUrl(app.url)?.hostname)) : null
  if (byHost) return byHost
  return (project.name && apps.find((app) => app.name === project.name)) || null
}

function cpuTone(cpu) {
  if (cpu === null) return 'slate'
  if (cpu < CPU_WARN) return 'emerald'
  return cpu < CPU_HOT ? 'amber' : 'rose'
}

export function replicaRows(fleetApp) {
  const replicas = Array.isArray(fleetApp?.replicas) ? fleetApp.replicas : []
  return replicas.filter(isObject).map((r) => {
    // Number(null) is 0, so reject null explicitly before coercing.
    const raw = r.cpu === null || r.cpu === undefined || r.cpu === '' ? NaN : Number(r.cpu)
    const cpu = Number.isFinite(raw) ? Math.round(raw) : null
    return { node: r.node ?? null, upstream: r.upstream ?? null, cpu, tone: cpuTone(cpu) }
  })
}

const SUMMARY_BUCKET = { live: 'live', attention: 'attention', failed: 'attention', deploying: 'deploying', stopped: 'stopped', cancelled: 'stopped' }

export function summarizeApps(projects, fleet) {
  const list = Array.isArray(projects) ? projects : []
  const summary = { total: list.length, live: 0, attention: 0, deploying: 0, stopped: 0, nodesOk: 0, nodesTotal: 0 }
  for (const p of list) summary[SUMMARY_BUCKET[appStatus(p).key]] += 1
  const nodes = Array.isArray(fleet?.nodes) ? fleet.nodes : []
  summary.nodesTotal = nodes.length
  summary.nodesOk = nodes.filter((n) => n?.ok === true).length
  return summary
}

const nameOf = (p) => (typeof p?.name === 'string' ? p.name : '')

export function sortApps(projects) {
  if (!Array.isArray(projects)) return []
  return [...projects].sort((a, b) => nameOf(a).localeCompare(nameOf(b), 'ko'))
}

export function deployMeta(project) {
  const p = asObject(project)
  const ref = typeof p.ref === 'string' && p.ref ? p.ref : null
  const triggerLabel = p.trigger === 'github-push' ? (ref ? `GitHub push · ${ref}` : 'GitHub push') : '수동 배포'
  return {
    triggerLabel,
    count: Array.isArray(p.history) ? p.history.length : 0,
    updatedAt: Number.isFinite(p.updated_at) ? p.updated_at : null,
  }
}

export function primaryUrl(targets) {
  const rows = Array.isArray(targets) ? targets : []
  return rows.find((r) => r?.state === 'serving' && r.url)?.url ?? null
}

const knownTargets = (keys) => [...new Set(keys.filter((k) => hasOwn(TARGET_META, k)))]

export function redeployRequest(project) {
  if (!isObject(project)) return null
  const { source, name, ref } = project
  if (typeof source !== 'string' || !source.trim()) return null
  // Without the stable name the backend would create a new app instead of redeploying this one.
  if (typeof name !== 'string' || !name) return null
  const listed = knownTargets(Array.isArray(project.targets) ? project.targets : [])
  const targets = listed.length ? listed : knownTargets(Object.keys(asObject(project.live)))
  if (!targets.length) return null
  const request = { source, targets, name }
  return typeof ref === 'string' && ref ? { ...request, ref } : request
}

export function canRedeploy(project, { runRunning = false, redeploying = false, stopping = false } = {}) {
  if (project?.last_status === 'running') return { ok: false, reason: '이 앱의 배포가 진행 중이에요' }
  if (stopping) return { ok: false, reason: '이 앱을 중지하는 중이에요' }
  if (runRunning) return { ok: false, reason: '진행 중인 배포가 끝나면 다시 배포할 수 있어요' }
  if (redeploying) return { ok: false, reason: '다시 배포를 요청하는 중이에요' }
  if (!redeployRequest(project)) return { ok: false, reason: '다시 배포할 소스 정보가 없어요' }
  return { ok: true, reason: null }
}

export function canOpenLogs(project, { runId = null, runRunning = false, launching = false, lookingUpLogs = false } = {}) {
  if (!project?.last_deployment_id) return { ok: false, reason: '조회할 배포 기록이 없어요' }
  if (launching) return { ok: false, reason: '배포를 요청하는 중이에요' }
  if (lookingUpLogs) return { ok: false, reason: '배포 기록을 확인하는 중이에요' }
  if (runRunning && project.last_deployment_id !== runId) {
    return { ok: false, reason: '진행 중인 배포가 끝나면 다른 앱의 로그를 볼 수 있어요' }
  }
  return { ok: true, reason: null }
}

export const EXPIRED_LOGS = '이 배포 기록을 찾을 수 없어요. 서버 재시작이나 보관 한도로 삭제됐을 수 있어요. 다시 배포하면 새 로그를 볼 수 있어요.'

