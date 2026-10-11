// Maps a deployment run to a header "weather" mood and describes each mood as plain data, so the 3D
// background stays a thin renderer and this file is testable in node.

export const WEATHER_STATUSES = ['idle', 'running', 'healing', 'completed', 'failed']

const FAILED_RUN_STATUSES = new Set(['failed', 'partial_failure'])

// Healing only counts while the run is still live; a finished run with a stale phase is not healing.
const isHealing = (run) => Object.values(run?.targetState || {}).some((s) => s?.phase === 'healing')

export function weatherStatus(run) {
  const status = run?.status
  if (status === 'running') return isHealing(run) ? 'healing' : 'running'
  if (status === 'completed') return 'completed'
  if (FAILED_RUN_STATUSES.has(status)) return 'failed'
  return 'idle'
}

// pattern: 'drift' floats in a wide band, 'swirl' orbits in rotating rings, 'ring' wraps the clouds and rises.
// spin is the ring rotation in radians per second; light.flash makes the accent light blink.
export const WEATHER_PRESETS = {
  idle: {
    cloud: { color: '#f8fafc', opacity: 0.6, speed: 0.15, volume: 1 },
    sparkles: { count: 40, color: '#38bdf8', size: 2, speed: 0.3, scale: [14, 2, 2], pattern: 'drift', spin: 0 },
    light: { color: '#e0f2fe', intensity: 0.6, flash: false },
    background: 'rgba(56, 189, 248, 0.04)',
  },
  running: {
    cloud: { color: '#c7d2fe', opacity: 0.5, speed: 0.4, volume: 0.9 },
    sparkles: { count: 96, color: '#818cf8', size: 3, speed: 1.2, scale: [1.6, 0.8, 1.6], pattern: 'swirl', spin: 1.1 },
    light: { color: '#a78bfa', intensity: 1.6, flash: false },
    background: 'rgba(99, 102, 241, 0.1)',
  },
  healing: {
    cloud: { color: '#d1fae5', opacity: 0.6, speed: 0.25, volume: 1 },
    sparkles: { count: 72, color: '#34d399', size: 3, speed: 0.6, scale: [1.2, 0.6, 1.2], pattern: 'ring', spin: 0.45 },
    light: { color: '#10b981', intensity: 1.4, flash: false },
    background: 'rgba(16, 185, 129, 0.08)',
  },
  completed: {
    cloud: { color: '#f8fafc', opacity: 0.6, speed: 0.15, volume: 1 },
    sparkles: { count: 40, color: '#38bdf8', size: 2.4, speed: 0.35, scale: [14, 2, 2], pattern: 'drift', spin: 0 },
    light: { color: '#fde68a', intensity: 0.8, flash: false },
    background: 'rgba(250, 204, 21, 0.05)',
  },
  failed: {
    cloud: { color: '#334155', opacity: 0.85, speed: 0.5, volume: 1.15 },
    sparkles: { count: 24, color: '#f87171', size: 1.5, speed: 0.4, scale: [14, 2, 2], pattern: 'drift', spin: 0 },
    light: { color: '#ef4444', intensity: 3, flash: true },
    background: 'rgba(30, 41, 59, 0.35)',
  },
}

export const presetFor = (status) => Object.hasOwn(WEATHER_PRESETS, status) ? WEATHER_PRESETS[status] : WEATHER_PRESETS.idle

export const PARALLAX_STRENGTH = 0.6
const POINTER_LIMIT = 1

const finiteOr = (v, fallback) => (Number.isFinite(v) ? v : fallback)
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))

// Pointer is normalised to -1..1 on both axes; out-of-range or non-numeric input is clamped or zeroed.
export function parallaxTarget(pointerX, pointerY, strength = PARALLAX_STRENGTH) {
  const s = Math.abs(finiteOr(strength, 0))
  const x = clamp(finiteOr(pointerX, 0), -POINTER_LIMIT, POINTER_LIMIT)
  const y = clamp(finiteOr(pointerY, 0), -POINTER_LIMIT, POINTER_LIMIT)
  return [x * s, y * s]
}

export const FLASH_HZ = 1.2
// Multiplying the sine before clamping flattens it into a soft-edged square wave.
const FLASH_SHARPNESS = 4

// Warning-light level in 0..1 at time t (seconds).
export function flashLevel(t) {
  const wave = Math.sin(2 * Math.PI * FLASH_HZ * finiteOr(t, 0))
  return clamp(0.5 + wave * FLASH_SHARPNESS, 0, 1)
}
