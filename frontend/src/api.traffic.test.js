import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getTrafficShadow } from './api';

const payload = { enabled: true, comparisons: [] };

describe('getTrafficShadow', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', {
      getItem: vi.fn(() => 'test-token'),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('uses the protected GET endpoint and existing token header', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => payload,
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(getTrafficShadow()).resolves.toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith('/traffic/shadow', {
      headers: expect.objectContaining({ 'X-API-Token': 'test-token' }),
    });
    expect(fetchMock.mock.calls[0][1].method).toBeUndefined();
  });

  it('uses the currently authenticated account token after an account switch', async () => {
    const localStorage = {
      getItem: vi.fn(() => 'alice-token'),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    };
    vi.stubGlobal('localStorage', localStorage);
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => payload,
    });
    vi.stubGlobal('fetch', fetchMock);

    await getTrafficShadow();
    localStorage.getItem.mockReturnValue('bob-token');
    await getTrafficShadow();

    expect(fetchMock.mock.calls.map(([, options]) => options.headers['X-API-Token'])).toEqual([
      'alice-token',
      'bob-token',
    ]);
  });

  it('distinguishes authentication, server, connection and invalid-response failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 403 }));
    await expect(getTrafficShadow()).rejects.toMatchObject({ kind: 'auth', status: 403 });

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500 }));
    await expect(getTrafficShadow()).rejects.toMatchObject({ kind: 'server', status: 500 });

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 }));
    await expect(getTrafficShadow()).rejects.toMatchObject({ kind: 'server', status: 503 });

    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    await expect(getTrafficShadow()).rejects.toMatchObject({ kind: 'network' });

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ comparisons: [] }) }));
    await expect(getTrafficShadow()).rejects.toMatchObject({ kind: 'response' });
  });
});
