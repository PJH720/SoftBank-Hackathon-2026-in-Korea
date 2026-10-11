// Pure helpers for the global fleet globe: geo maths, region matching and pillar visuals.
// Plain arrays only, so everything here is testable in node without three.
import { NODE_LOAD_HOT, STATUS_STYLE, classifyNode, loadRatio } from './fleetStatus'

export const GLOBE_RADIUS = 1
export const PILLAR_BASE = 0.06
export const PILLAR_SPAN = 0.55
export const NO_DATA_COLOR = '#64748b'
export const NO_DATA_LABEL = '데이터 없음'
export const LOCAL_SLOT_ID = 'local'
// Marker sizes live here so tests can check that neighbouring slots never share a hit area.
export const PIN_RADIUS = 0.034
export const HIT_RADIUS = 0.045

const DEG = Math.PI / 180

// Spherical coordinates with +Y as the north pole and lon 0 on the +X axis.
export function latLonToVector3(lat, lon, radius = GLOBE_RADIUS) {
  const phi = (90 - lat) * DEG
  const theta = (lon + 180) * DEG
  return [
    -radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.cos(phi),
    radius * Math.sin(phi) * Math.sin(theta),
  ]
}

export const surfaceNormal = (lat, lon) => latLonToVector3(lat, lon, 1)

// The local slot sits offshore south-west of Seoul, far enough that the two hit areas never overlap.
export const REGION_SLOTS = [
  { id: 'seoul', label: '서울', region: 'asia-northeast3', lat: 37.5665, lon: 126.978, tokens: ['seoul', 'asia-northeast3', 'ap-northeast-2', 'icn'] },
  { id: 'tokyo', label: '도쿄', region: 'asia-northeast1', lat: 35.6762, lon: 139.6503, tokens: ['tokyo', 'asia-northeast1', 'ap-northeast-1', 'nrt'] },
  { id: 'singapore', label: '싱가포르', region: 'asia-southeast1', lat: 1.3521, lon: 103.8198, tokens: ['singapore', 'asia-southeast1', 'ap-southeast-1', 'sin', 'sg'] },
  { id: 'virginia', label: '버지니아', region: 'us-east4', lat: 39.0438, lon: -77.4874, tokens: ['virginia', 'us-east4', 'us-east-1', 'iad'] },
  { id: LOCAL_SLOT_ID, label: '로컬 핑', region: 'local', lat: 32, lon: 122.5, tokens: [], local: true },
]

const escapeRegExp = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

// Whole-token match: 'sg' hits 'aws-sg-1' but not 'gcp-sgx'; 'us-east4' never hits 'us-east-1'.
const tokenPattern = (token) => new RegExp(`(^|[^a-z0-9])${escapeRegExp(token.toLowerCase())}($|[^a-z0-9])`)
const SLOT_PATTERNS = new Map(REGION_SLOTS.map((s) => [s.id, s.tokens.map(tokenPattern)]))

const MATCH_FIELDS = ['node', 'region', 'zone']

export function nodeMatchesSlot(node, slot) {
  if (!node || slot.local) return false
  const patterns = SLOT_PATTERNS.get(slot.id) || slot.tokens.map(tokenPattern)
  return MATCH_FIELDS.some((field) => {
    const value = node[field]
    if (typeof value !== 'string' || value === '') return false
    const text = field === 'zone' ? value.toLowerCase().replace(/^((?:ap|us)-[a-z]+-\d)[a-z]$/, '$1') : value.toLowerCase()
    return patterns.some((re) => re.test(text))
  })
}

// Accepts the /fleet payload ({ nodes }) or a bare node array.
export function fleetNodes(fleet) {
  if (Array.isArray(fleet)) return fleet
  return Array.isArray(fleet?.nodes) ? fleet.nodes : []
}

// First matching node wins a slot. Nodes that match no slot, or lose to an earlier node, count as unplaced.
export function matchNodesToSlots(fleet, slots = REGION_SLOTS) {
  const taken = new Map()
  let unplaced = 0
  for (const node of fleetNodes(fleet)) {
    if (!node) continue
    // Explicit location beats a friendly name that may be stale after migration.
    const location = typeof node.region === 'string' && node.region
      ? { region: node.region }
      : typeof node.zone === 'string' && node.zone ? { zone: node.zone } : { node: node.node }
    const slot = slots.find((s) => nodeMatchesSlot(location, s))
    if (!slot || taken.has(slot.id)) {
      unplaced += 1
      continue
    }
    taken.set(slot.id, node)
  }
  return { slots: slots.map((slot) => ({ slot, node: taken.get(slot.id) ?? null })), unplaced }
}

const clamp01 = (v) => Math.min(1, Math.max(0, v))

export const pillarHeight = (ratio) => PILLAR_BASE + PILLAR_SPAN * clamp01(Number.isFinite(ratio) ? ratio : 0)

export function memoryRatio(node) {
  const total = node?.mem_total_mb
  const avail = node?.mem_avail_mb
  if (!node?.ok || !Number.isFinite(total) || !Number.isFinite(avail) || total <= 0) return null
  return clamp01(1 - avail / total)
}

// A normalized load average can exceed 100%; it is not measured CPU utilization.
export function slotVisual(node) {
  if (!node) {
    return { status: 'none', hasData: false, color: NO_DATA_COLOR, label: NO_DATA_LABEL, height: PILLAR_BASE, cpuRatio: null, memRatio: null, cpuPct: null, memPct: null }
  }
  let status = classifyNode(node)
  const style = STATUS_STYLE[status]
  if (!node.ok) {
    return { status, hasData: true, color: style.color, label: style.label, height: PILLAR_BASE, cpuRatio: null, memRatio: null, cpuPct: null, memPct: null }
  }
  const cpuRatio = Number.isFinite(node.load1) && Number.isFinite(node.cpus) && node.cpus > 0 ? loadRatio(node) : null
  const memRatio = memoryRatio(node)
  const pressure = Math.max(cpuRatio ?? 0, memRatio ?? 0)
  status = pressure >= NODE_LOAD_HOT ? 'overloaded' : 'healthy'
  const hasMetrics = cpuRatio != null || memRatio != null
  const mix = clamp01(pressure / NODE_LOAD_HOT)
  const channels = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))
  const healthy = channels(STATUS_STYLE.healthy.color)
  const hot = channels(STATUS_STYLE.overloaded.color)
  const color = `#${healthy.map((v, i) => Math.round(v + (hot[i] - v) * mix).toString(16).padStart(2, '0')).join('')}`
  return {
    status,
    hasData: true,
    color: hasMetrics ? color : NO_DATA_COLOR,
    label: STATUS_STYLE[status].label,
    height: pillarHeight(pressure),
    cpuRatio,
    memRatio,
    cpuPct: cpuRatio == null ? null : Math.round(cpuRatio * 100),
    memPct: memRatio == null ? null : Math.round(memRatio * 100),
  }
}

// The local slot reports backend reachability only, never load.
export function localVisual(healthy) {
  const base = { hasData: healthy != null, height: PILLAR_BASE, cpuRatio: null, memRatio: null, cpuPct: null, memPct: null }
  if (healthy === true) return { ...base, status: 'healthy', color: STATUS_STYLE.healthy.color, label: '연결됨' }
  if (healthy === false) return { ...base, status: 'failed', color: STATUS_STYLE.failed.color, label: '끊김' }
  return { ...base, status: 'none', color: NO_DATA_COLOR, label: '확인 중' }
}

export const gaugeWidth = (pct) => (Number.isFinite(pct) ? Math.min(100, Math.max(0, pct)) : 0)

// Fit the complete globe plus pillars within the narrower camera field of view.
export function overviewDistance(aspect, fov = 45) {
  const halfVertical = fov * DEG / 2
  const halfHorizontal = Math.atan(Math.tan(halfVertical) * Math.max(0.1, aspect))
  const radius = GLOBE_RADIUS + PILLAR_BASE + PILLAR_SPAN + PIN_RADIUS
  return radius / Math.sin(Math.min(halfVertical, halfHorizontal)) * 1.1
}

// Flat [x,y,z, x,y,z, ...] line-segment pairs for latitude rings and longitude meridians.
export function graticulePositions(radius = GLOBE_RADIUS, latStep = 30, lonStep = 30, segments = 64) {
  const out = []
  const push = (a, b) => out.push(a[0], a[1], a[2], b[0], b[1], b[2])
  for (let lat = -90 + latStep; lat < 90; lat += latStep) {
    for (let i = 0; i < segments; i++) {
      const lon0 = -180 + (360 * i) / segments
      const lon1 = -180 + (360 * (i + 1)) / segments
      push(latLonToVector3(lat, lon0, radius), latLonToVector3(lat, lon1, radius))
    }
  }
  const half = segments / 2
  for (let lon = -180; lon < 180; lon += lonStep) {
    for (let i = 0; i < half; i++) {
      const lat0 = -90 + (180 * i) / half
      const lat1 = -90 + (180 * (i + 1)) / half
      push(latLonToVector3(lat0, lon, radius), latLonToVector3(lat1, lon, radius))
    }
  }
  return out
}

// Cosine between the surface normal at `point` (globe centred on the origin) and the direction to the camera.
// Negative means the point is on the far side of the globe.
export function facingDot(point, camera) {
  const pl = Math.hypot(point[0], point[1], point[2])
  const dx = camera[0] - point[0]
  const dy = camera[1] - point[1]
  const dz = camera[2] - point[2]
  const dl = Math.hypot(dx, dy, dz)
  if (pl === 0 || dl === 0) return 1
  return (point[0] * dx + point[1] * dy + point[2] * dz) / (pl * dl)
}
