import { afterEach, describe, expect, it, vi } from 'vitest'
import { normalizeLlmConfig } from './llmConfig'
import { startDeploy } from '../api'

afterEach(() => vi.unstubAllGlobals())

describe('dashboard model override', () => {
  it.each([undefined, null, { provider: 'server' }])('omits server defaults for %j', (selection) => {
    expect(normalizeLlmConfig(selection)).toBeUndefined()
  })

  it.each(['anthropic', 'openai'])('normalizes optional %s model names', (provider) => {
    expect(normalizeLlmConfig({ provider, model: '  ' })).toEqual({ provider })
    expect(normalizeLlmConfig({ provider, model: ' selected-model ' })).toEqual({ provider, model: 'selected-model' })
    expect(() => normalizeLlmConfig({ provider, base_url: 'https://example.com' })).toThrow(/URL/)
  })

  it('requires a local model and a valid HTTP(S) endpoint', () => {
    expect(() => normalizeLlmConfig({ provider: 'local', model: ' ' })).toThrow(/모델명/)
    for (const base_url of ['', 'invalid', 'ftp://localhost/v1']) {
      expect(() => normalizeLlmConfig({ provider: 'local', model: 'qwen', base_url })).toThrow(/HTTP/)
    }
    expect(normalizeLlmConfig({ provider: 'local', model: ' qwen ', base_url: ' http://localhost:11434/v1 ' }))
      .toEqual({ provider: 'local', model: 'qwen', base_url: 'http://localhost:11434/v1' })
  })

  it('rejects unknown providers', () => {
    expect(() => normalizeLlmConfig({ provider: 'unknown' })).toThrow(/제공자/)
  })

  it('sends the optional override in the actual deployment request, preserving existing defaults', async () => {
    vi.stubGlobal('localStorage', { getItem: () => null })
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ deployment_id: 'deployment-1' }) })
    vi.stubGlobal('fetch', fetch)
    const request = { source: 'sample-apps/broken', targets: ['local'] }
    expect(await startDeploy(request)).toBe('deployment-1')
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ ...request, ask_env: true })
    await startDeploy({ ...request, llm_config: { provider: 'openai', model: ' chosen ' } })
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ ...request, ask_env: true, llm_config: { provider: 'openai', model: 'chosen' } })
    await expect(startDeploy({ ...request, llm_config: { provider: 'local' } })).rejects.toThrow(/모델명/)
    expect(fetch).toHaveBeenCalledTimes(2)
  })
})
