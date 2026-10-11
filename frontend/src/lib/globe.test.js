import { describe, it, expect } from 'vitest'
import { STATUS_STYLE, demoNodes } from './fleetStatus'
import {
  GLOBE_RADIUS,
  HIT_RADIUS,
  PIN_RADIUS,
  LOCAL_SLOT_ID,
  NO_DATA_COLOR,
  NO_DATA_LABEL,
  PILLAR_BASE,
  PILLAR_SPAN,
  REGION_SLOTS,
  facingDot,
  fleetNodes,
  gaugeWidth,
  graticulePositions,
  latLonToVector3,
  localVisual,
  matchNodesToSlots,
  memoryRatio,
  nodeMatchesSlot,
  overviewDistance,
  pillarHeight,
  slotVisual,
  surfaceNormal,
} from './globe'

const expectVec = (actual, expected) => {
  expect(actual).toHaveLength(3)
  expected.forEach((v, i) => expect(actual[i]).toBeCloseTo(v, 10))
}

const slotById = (id) => REGION_SLOTS.find((s) => s.id === id)
const nodeFor = (result, id) => result.slots.find((s) => s.slot.id === id).node

describe('latLonToVector3', () => {
  it('puts the north pole on +y and the south pole on -y', () => {
    expectVec(latLonToVector3(90, 0, 1), [0, 1, 0])
    expectVec(latLonToVector3(-90, 45, 1), [0, -1, 0])
  })

  it('places the equator at lon 0 on +x', () => {
    expectVec(latLonToVector3(0, 0, 1), [1, 0, 0])
  })

  it('places the equator at lon 90 on -z and lon -90 on +z', () => {
    expectVec(latLonToVector3(0, 90, 1), [0, 0, -1])
    expectVec(latLonToVector3(0, -90, 1), [0, 0, 1])
  })

  it('scales linearly with the radius', () => {
    const unit = latLonToVector3(37.5665, 126.978, 1)
    const big = latLonToVector3(37.5665, 126.978, 2.5)
    expectVec(big, unit.map((v) => v * 2.5))
    expect(Math.hypot(...big)).toBeCloseTo(2.5, 10)
  })

  it('defaults to the globe radius', () => {
    expect(Math.hypot(...latLonToVector3(10, 20))).toBeCloseTo(GLOBE_RADIUS, 10)
  })

  it('returns a unit surface normal', () => {
    expect(Math.hypot(...surfaceNormal(-33.8, 151.2))).toBeCloseTo(1, 10)
  })
})

describe('REGION_SLOTS', () => {
  it('lists the five deploy locations with unique ids', () => {
    expect(REGION_SLOTS.map((s) => s.id)).toEqual(['seoul', 'tokyo', 'singapore', 'virginia', LOCAL_SLOT_ID])
  })

  it('keeps every pair of slots further apart than two hit radii', () => {
    expect(HIT_RADIUS).toBeGreaterThan(PIN_RADIUS)
    for (let i = 0; i < REGION_SLOTS.length; i++) {
      for (let j = i + 1; j < REGION_SLOTS.length; j++) {
        const a = latLonToVector3(REGION_SLOTS[i].lat, REGION_SLOTS[i].lon, GLOBE_RADIUS)
        const b = latLonToVector3(REGION_SLOTS[j].lat, REGION_SLOTS[j].lon, GLOBE_RADIUS)
        expect(Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2])).toBeGreaterThan(2 * HIT_RADIUS)
      }
    }
  })
})

describe('matchNodesToSlots', () => {
  it('matches the demo fleet names and counts osaka as unplaced', () => {
    // Arrange
    const fleet = { nodes: [
      { node: 'gcp-seoul', ok: true, load1: 0.4, cpus: 2 },
      { node: 'aws-tokyo', ok: true, load1: 3.5, cpus: 2 },
      { node: 'oci-osaka', ok: false },
    ] }
    // Act
    const result = matchNodesToSlots(fleet)
    // Assert
    expect(nodeFor(result, 'seoul').node).toBe('gcp-seoul')
    expect(nodeFor(result, 'tokyo').node).toBe('aws-tokyo')
    expect(nodeFor(result, 'singapore')).toBeNull()
    expect(nodeFor(result, 'virginia')).toBeNull()
    expect(result.unplaced).toBe(1)
  })

  it('matches region codes and airport codes case-insensitively', () => {
    const fleet = [
      { node: 'worker-a', region: 'ASIA-NORTHEAST3' },
      { node: 'NRT-edge-1' },
      { node: 'edge', zone: 'asia-southeast1-b' },
      { node: 'aws-iad-7' },
    ]
    const result = matchNodesToSlots(fleet)
    expect(nodeFor(result, 'seoul').node).toBe('worker-a')
    expect(nodeFor(result, 'tokyo').node).toBe('NRT-edge-1')
    expect(nodeFor(result, 'singapore').node).toBe('edge')
    expect(nodeFor(result, 'virginia').node).toBe('aws-iad-7')
    expect(result.unplaced).toBe(0)
  })

  it('only matches whole tokens', () => {
    expect(nodeMatchesSlot({ node: 'aws-sg-1' }, slotById('singapore'))).toBe(true)
    expect(nodeMatchesSlot({ node: 'gcp-sgx-1' }, slotById('singapore'))).toBe(false)
    expect(nodeMatchesSlot({ node: 'basin-1' }, slotById('singapore'))).toBe(false)
    expect(nodeMatchesSlot({ node: 'aws-us-east-1' }, slotById('virginia'))).toBe(true)
    expect(nodeMatchesSlot({ node: 'aws-us-east-11' }, slotById('virginia'))).toBe(false)
  })

  it('maps provider region codes and gives explicit location priority over a stale name', () => {
    const result = matchNodesToSlots([
      { node: 'aws-tokyo', region: 'us-east-1' },
      { node: 'worker-a', region: 'ap-northeast-2' },
      { node: 'worker-b', zone: 'ap-northeast-1a' },
      { node: 'worker-c', region: 'ap-southeast-1' },
    ])
    expect(nodeFor(result, 'virginia').node).toBe('aws-tokyo')
    expect(nodeFor(result, 'seoul').node).toBe('worker-a')
    expect(nodeFor(result, 'singapore').node).toBe('worker-c')
    expect(nodeFor(result, 'tokyo').node).toBe('worker-b')
    expect(result.unplaced).toBe(0)
  })

  it('leaves an unknown explicit location unplaced rather than using a misleading name', () => {
    const result = matchNodesToSlots([{ node: 'gcp-seoul', region: 'europe-west1' }])
    expect(nodeFor(result, 'seoul')).toBeNull()
    expect(result.unplaced).toBe(1)
  })

  it('never assigns a fleet node to the local slot', () => {
    expect(nodeMatchesSlot({ node: 'local' }, slotById(LOCAL_SLOT_ID))).toBe(false)
    const result = matchNodesToSlots([{ node: 'local' }])
    expect(nodeFor(result, LOCAL_SLOT_ID)).toBeNull()
    expect(result.unplaced).toBe(1)
  })

  it('keeps the first node per slot and counts the rest as unplaced', () => {
    const result = matchNodesToSlots([{ node: 'gcp-seoul-a' }, { node: 'gcp-seoul-b' }])
    expect(nodeFor(result, 'seoul').node).toBe('gcp-seoul-a')
    expect(result.unplaced).toBe(1)
  })

  it('handles a missing fleet, null entries and non-string fields', () => {
    expect(matchNodesToSlots(null).unplaced).toBe(0)
    expect(matchNodesToSlots(null).slots.every((s) => s.node === null)).toBe(true)
    const result = matchNodesToSlots({ nodes: [null, { node: 42, region: null }] })
    expect(result.unplaced).toBe(1)
  })

  it('reads either the payload or a bare array', () => {
    expect(fleetNodes({ nodes: [1] })).toEqual([1])
    expect(fleetNodes([2])).toEqual([2])
    expect(fleetNodes(undefined)).toEqual([])
  })

  it('leaves synthetic demo nodes unplaced', () => {
    expect(matchNodesToSlots(demoNodes(20)).unplaced).toBe(20)
  })
})

describe('slotVisual', () => {
  it('shows a grey minimal pillar and no numbers when no node matched', () => {
    const v = slotVisual(null)
    expect(v).toMatchObject({ status: 'none', hasData: false, color: NO_DATA_COLOR, label: NO_DATA_LABEL, height: PILLAR_BASE, cpuPct: null, memPct: null })
  })

  it('never invents load or memory for an unreachable node', () => {
    // demoNodes reports failed nodes with mem_avail_mb 0, which would otherwise read as 100% memory.
    const v = slotVisual({ node: 'x', ok: false, load1: 0, cpus: 2, mem_total_mb: 4096, mem_avail_mb: 0 })
    expect(v.status).toBe('failed')
    expect(v.color).toBe(STATUS_STYLE.failed.color)
    expect(v.height).toBe(PILLAR_BASE)
    expect(v.cpuPct).toBeNull()
    expect(v.memPct).toBeNull()
  })

  it('uses status colours at the load gradient endpoints', () => {
    expect(slotVisual({ ok: true, load1: 0, cpus: 2 }).color).toBe(STATUS_STYLE.healthy.color)
    expect(slotVisual({ ok: true, load1: 3, cpus: 2 }).color).toBe(STATUS_STYLE.overloaded.color)
    expect(slotVisual({ ok: true, load1: 3, cpus: 2 }).label).toBe(STATUS_STYLE.overloaded.label)
  })

  it('changes colour with both CPU and memory pressure', () => {
    const calm = slotVisual({ ok: true, load1: 0.2, cpus: 2 })
    const busy = slotVisual({ ok: true, load1: 1.2, cpus: 2 })
    expect(calm.color).not.toBe(busy.color)
    const memoryHot = slotVisual({ ok: true, load1: 0.2, cpus: 2, mem_total_mb: 1000, mem_avail_mb: 100 })
    expect(memoryHot.status).toBe('overloaded')
    expect(memoryHot.color).toBe(STATUS_STYLE.overloaded.color)
    expect(memoryHot.height).toBeGreaterThan(busy.height)
  })

  it('does not invent load or memory when metrics are missing or null', () => {
    for (const node of [{ ok: true }, { ok: true, load1: null, cpus: 2, mem_total_mb: 1000, mem_avail_mb: null }, { ok: true, load1: 2, cpus: 0 }]) {
      expect(slotVisual(node)).toMatchObject({ cpuPct: null, memPct: null, color: NO_DATA_COLOR, height: PILLAR_BASE })
    }
    expect(slotVisual({ ok: true, mem_total_mb: 1000, mem_avail_mb: 0 })).toMatchObject({ cpuPct: null, memPct: 100, status: 'overloaded' })
  })

  it('grows the pillar monotonically with load and caps it', () => {
    const heights = [0, 0.25, 0.5, 0.75, 1, 2].map((r) => slotVisual({ ok: true, load1: r * 4, cpus: 4 }).height)
    for (let i = 1; i < heights.length; i++) expect(heights[i]).toBeGreaterThanOrEqual(heights[i - 1])
    expect(heights[0]).toBe(PILLAR_BASE)
    expect(heights[4]).toBeCloseTo(PILLAR_BASE + PILLAR_SPAN, 10)
    expect(heights[5]).toBeCloseTo(PILLAR_BASE + PILLAR_SPAN, 10)
  })

  it('uses whichever of cpu or memory is higher for the height', () => {
    const v = slotVisual({ ok: true, load1: 0.2, cpus: 2, mem_total_mb: 1000, mem_avail_mb: 250 })
    expect(v.cpuPct).toBe(10)
    expect(v.memPct).toBe(75)
    expect(v.height).toBeCloseTo(pillarHeight(0.75), 10)
  })

  it('reports cpu above 100% for an overloaded node', () => {
    expect(slotVisual({ ok: true, load1: 3, cpus: 2 }).cpuPct).toBe(150)
  })

  it('returns null memory when fields are missing or the total is zero', () => {
    expect(memoryRatio({ ok: true })).toBeNull()
    expect(memoryRatio({ ok: true, mem_total_mb: 0, mem_avail_mb: 0 })).toBeNull()
    expect(memoryRatio({ ok: true, mem_total_mb: 1000 })).toBeNull()
    expect(slotVisual({ ok: true, load1: 1, cpus: 2 }).memPct).toBeNull()
  })

  it('clamps memory into 0..1', () => {
    expect(memoryRatio({ ok: true, mem_total_mb: 1000, mem_avail_mb: 1500 })).toBe(0)
  })
})

describe('localVisual', () => {
  it('maps backend health to a connection label without load numbers', () => {
    expect(localVisual(true)).toMatchObject({ label: '연결됨', color: STATUS_STYLE.healthy.color, cpuPct: null })
    expect(localVisual(false)).toMatchObject({ label: '끊김', color: STATUS_STYLE.failed.color, cpuPct: null })
    expect(localVisual(null)).toMatchObject({ label: '확인 중', color: NO_DATA_COLOR, hasData: false })
    expect(localVisual(undefined).label).toBe('확인 중')
  })
})

describe('gaugeWidth', () => {
  it('clamps to 0..100 and treats null as empty', () => {
    expect(gaugeWidth(150)).toBe(100)
    expect(gaugeWidth(-5)).toBe(0)
    expect(gaugeWidth(42)).toBe(42)
    expect(gaugeWidth(null)).toBe(0)
  })
})

describe('overviewDistance', () => {
  it('fits the tallest pillar and pin inside a portrait camera field of view', () => {
    const aspect = 0.4
    const halfFov = Math.atan(Math.tan(Math.PI / 8) * aspect)
    const distance = overviewDistance(aspect)
    const radius = GLOBE_RADIUS + PILLAR_BASE + PILLAR_SPAN + PIN_RADIUS
    expect(Math.asin(radius / distance)).toBeLessThan(halfFov)
    expect(distance).toBeGreaterThan(overviewDistance(1))
    expect(overviewDistance(2)).toBe(overviewDistance(1))
  })
})

describe('graticulePositions', () => {
  it('emits whole segments that lie on the sphere', () => {
    const pts = graticulePositions(2, 30, 30, 16)
    expect(pts.length % 6).toBe(0)
    for (let i = 0; i < pts.length; i += 3) expect(Math.hypot(pts[i], pts[i + 1], pts[i + 2])).toBeCloseTo(2, 8)
  })

  it('draws 5 latitude rings and 12 meridians at 30 degree steps', () => {
    const segments = 16
    const pts = graticulePositions(1, 30, 30, segments)
    expect(pts.length / 6).toBe(5 * segments + 12 * (segments / 2))
  })
})

describe('facingDot', () => {
  it('is positive on the near side and negative on the far side', () => {
    expect(facingDot([0, 0, 1], [0, 0, 5])).toBeCloseTo(1, 10)
    expect(facingDot([0, 0, -1], [0, 0, 5])).toBeCloseTo(-1, 10)
  })

  it('is near zero on the limb', () => {
    expect(Math.abs(facingDot([1, 0, 0], [0, 0, 50]))).toBeLessThan(0.05)
  })

  it('treats degenerate input as visible', () => {
    expect(facingDot([0, 0, 0], [0, 0, 5])).toBe(1)
  })
})
