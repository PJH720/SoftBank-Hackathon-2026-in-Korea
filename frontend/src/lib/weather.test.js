import { describe, it, expect } from 'vitest'
import {
  FLASH_HZ,
  PARALLAX_STRENGTH,
  WEATHER_PRESETS,
  WEATHER_STATUSES,
  flashLevel,
  parallaxTarget,
  presetFor,
  weatherStatus,
} from './weather'

const makeRun = (status, phases = []) => ({
  status,
  targetState: Object.fromEntries(phases.map((phase, i) => [`t${i}`, { phase }])),
})

describe('weatherStatus', () => {
  it('maps a missing run to idle', () => {
    expect(weatherStatus(null)).toBe('idle')
    expect(weatherStatus(undefined)).toBe('idle')
    expect(weatherStatus({})).toBe('idle')
  })

  it('maps idle and cancelled runs to idle', () => {
    expect(weatherStatus(makeRun('idle'))).toBe('idle')
    expect(weatherStatus(makeRun('cancelled', ['cancelled']))).toBe('idle')
  })

  it('maps an unknown status to idle', () => {
    expect(weatherStatus(makeRun('exploded'))).toBe('idle')
  })

  it('maps a running run without healing targets to running', () => {
    // Arrange
    const run = makeRun('running', ['deploying', 'pending', 'done'])
    // Act
    const weather = weatherStatus(run)
    // Assert
    expect(weather).toBe('running')
  })

  it('maps a running run with no targetState to running', () => {
    expect(weatherStatus({ status: 'running' })).toBe('running')
  })

  it('maps a running run with any healing target to healing', () => {
    expect(weatherStatus(makeRun('running', ['done', 'healing']))).toBe('healing')
  })

  it('ignores null target entries while looking for healing', () => {
    const run = { status: 'running', targetState: { a: null, b: { phase: 'healing' } } }
    expect(weatherStatus(run)).toBe('healing')
    expect(weatherStatus({ status: 'running', targetState: { a: null } })).toBe('running')
  })

  it('keeps a completed run completed even with a stale healing phase', () => {
    expect(weatherStatus(makeRun('completed', ['healing']))).toBe('completed')
  })

  it('maps failed and partial_failure runs to failed', () => {
    expect(weatherStatus(makeRun('failed', ['failed']))).toBe('failed')
    expect(weatherStatus(makeRun('partial_failure', ['done', 'failed']))).toBe('failed')
  })
})

describe('WEATHER_PRESETS', () => {
  const keysOf = (o) => Object.keys(o).sort()

  it('defines a preset for every weather status', () => {
    expect(keysOf(WEATHER_PRESETS)).toEqual([...WEATHER_STATUSES].sort())
  })

  it('gives every status the same top-level and nested keys', () => {
    // Arrange
    const reference = WEATHER_PRESETS.idle
    for (const status of WEATHER_STATUSES) {
      const preset = WEATHER_PRESETS[status]
      // Assert
      expect(keysOf(preset)).toEqual(keysOf(reference))
      expect(keysOf(preset.cloud)).toEqual(keysOf(reference.cloud))
      expect(keysOf(preset.sparkles)).toEqual(keysOf(reference.sparkles))
      expect(keysOf(preset.light)).toEqual(keysOf(reference.light))
    }
  })

  it('uses calm white clouds at 0.6 opacity and 40 sky-blue sparkles when idle', () => {
    expect(WEATHER_PRESETS.idle.cloud.opacity).toBe(0.6)
    expect(WEATHER_PRESETS.idle.sparkles.count).toBe(40)
    expect(WEATHER_PRESETS.idle.sparkles.color).toBe('#38bdf8')
    expect(WEATHER_PRESETS.idle.light.flash).toBe(false)
  })

  it('keeps completed calm with white 0.6 clouds and 40 blue sparkles', () => {
    expect(WEATHER_PRESETS.completed.cloud.color).toBe(WEATHER_PRESETS.idle.cloud.color)
    expect(WEATHER_PRESETS.completed.cloud.opacity).toBe(0.6)
    expect(WEATHER_PRESETS.completed.sparkles.count).toBe(40)
    expect(WEATHER_PRESETS.completed.sparkles.color).toBe('#38bdf8')
    expect(WEATHER_PRESETS.completed.sparkles.pattern).toBe('drift')
    expect(WEATHER_PRESETS.completed.light.flash).toBe(false)
  })

  it('swirls more and faster indigo particles while running', () => {
    const { running, idle } = WEATHER_PRESETS
    expect(running.sparkles.pattern).toBe('swirl')
    expect(running.sparkles.count).toBeGreaterThan(idle.sparkles.count)
    expect(running.sparkles.speed).toBeGreaterThan(idle.sparkles.speed)
    expect(running.sparkles.spin).toBeGreaterThan(0)
  })

  it('wraps the clouds in an emerald ring while healing', () => {
    expect(WEATHER_PRESETS.healing.sparkles.pattern).toBe('ring')
    expect(WEATHER_PRESETS.healing.sparkles.color).toBe('#34d399')
  })

  it('uses dark storm clouds and a flashing red light when failed', () => {
    expect(WEATHER_PRESETS.failed.cloud.color).toBe('#334155')
    expect(WEATHER_PRESETS.failed.light.color).toBe('#ef4444')
    expect(WEATHER_PRESETS.failed.light.flash).toBe(true)
  })

  it('caps every sparkle system at 120 particles', () => {
    for (const status of WEATHER_STATUSES) expect(WEATHER_PRESETS[status].sparkles.count).toBeLessThanOrEqual(120)
  })

  it('falls back to idle for an unknown status', () => {
    expect(presetFor('nope')).toBe(WEATHER_PRESETS.idle)
    expect(presetFor('toString')).toBe(WEATHER_PRESETS.idle)
    expect(presetFor('__proto__')).toBe(WEATHER_PRESETS.idle)
    expect(presetFor('failed')).toBe(WEATHER_PRESETS.failed)
  })
})

describe('parallaxTarget', () => {
  it('returns no offset for a centred pointer', () => {
    expect(parallaxTarget(0, 0, 1)).toEqual([0, 0])
  })

  it('scales the pointer by strength', () => {
    expect(parallaxTarget(0.5, -1, 2)).toEqual([1, -2])
  })

  it('uses the default strength when none is given', () => {
    expect(parallaxTarget(1, 1)).toEqual([PARALLAX_STRENGTH, PARALLAX_STRENGTH])
  })

  it('clamps an out-of-range pointer to -1..1', () => {
    expect(parallaxTarget(5, -9, 1)).toEqual([1, -1])
  })

  it('treats NaN or missing coordinates as centred', () => {
    expect(parallaxTarget(NaN, undefined, 1)).toEqual([0, 0])
    expect(parallaxTarget(0.5, 0.5, NaN)).toEqual([0, 0])
  })
})

describe('flashLevel', () => {
  const period = 1 / FLASH_HZ

  it('is fully on at the crest and fully off at the trough', () => {
    expect(flashLevel(period / 4)).toBe(1)
    expect(flashLevel((3 * period) / 4)).toBe(0)
  })

  it('stays within 0..1 over a full period', () => {
    for (let i = 0; i <= 40; i += 1) {
      const level = flashLevel((i / 40) * period)
      expect(level).toBeGreaterThanOrEqual(0)
      expect(level).toBeLessThanOrEqual(1)
    }
  })

  it('treats a non-numeric time as zero', () => {
    expect(flashLevel(undefined)).toBe(flashLevel(0))
  })
})
