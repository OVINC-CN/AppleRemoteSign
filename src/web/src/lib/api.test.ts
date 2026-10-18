import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api, apiUrl, setCsrfToken } from './api';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
describe('runtime API configuration', () => {
  it('uses the deployment config without a rebuild', () => {
    window.__APP_CONFIG__ = {
      API_BASE_UR: 'https://first.example/',
    };
    expect(apiUrl('/api/v1/me')).toBe('https://first.example/api/v1/me');
    window.__APP_CONFIG__.API_BASE_UR = 'https://second.example';
    expect(apiUrl('/api/v1/me')).toBe('https://second.example/api/v1/me');
  });
  it('rejects invalid public config', () => {
    window.__APP_CONFIG__ = {
      API_BASE_UR: 'javascript:alert(1)',
    };
    expect(() => apiUrl('/api/v1/me')).toThrow();
    delete window.__APP_CONFIG__;
    expect(() => apiUrl('/api/v1/me')).toThrow('缺少 API 配置');
  });
  it('includes cookies and a CSRF token for mutations', async () => {
    window.__APP_CONFIG__ = {
      API_BASE_UR: '',
    };
    setCsrfToken('csrf');
    const fetch = vi.fn().mockResolvedValue({
      status: 200,
      json: async () => ({
        data: {
          ok: true,
        },
      }),
    });
    vi.stubGlobal('fetch', fetch);
    expect(
      await api('/api/v1/test', {
        method: 'POST',
        body: '{}',
      }),
    ).toEqual({
      ok: true,
    });
    const options = fetch.mock.calls[0][1];
    expect(options.credentials).toBe('include');
    expect(options.headers.get('X-CSRF-Token')).toBe('csrf');
  });
  it('retains the job id on a signing failure without retrying', async () => {
    window.__APP_CONFIG__ = {
      API_BASE_UR: '',
    };
    const fetch = vi.fn().mockResolvedValue({
      status: 500,
      json: async () => ({
        data: {
          id: 'job-1',
          status: 'failed',
        },
        error_message: 'sign failed',
      }),
    });
    vi.stubGlobal('fetch', fetch);
    try {
      await api('/api/v1/jobs', {
        method: 'POST',
      });
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).data?.id).toBe('job-1');
    }
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it('aborts a stalled request on timeout and does not retry', async () => {
    vi.useFakeTimers();
    window.__APP_CONFIG__ = {
      API_BASE_UR: '',
    };
    let signal!: AbortSignal;
    const fetch = vi.fn(
      (_url, options) =>
        new Promise((_resolve, reject) => {
          signal = options.signal;
          signal.addEventListener('abort', () =>
            reject(new DOMException('Aborted', 'AbortError')),
          );
        }),
    );
    vi.stubGlobal('fetch', fetch);
    const result = expect(api('/api/v1/jobs')).rejects.toThrow('请求超时');
    await vi.advanceTimersByTimeAsync(30000);
    await result;
    expect(signal.aborted).toBe(true);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it('cancels a pending request when its caller leaves', async () => {
    window.__APP_CONFIG__ = {
      API_BASE_UR: '',
    };
    let signal!: AbortSignal;
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url, options) =>
          new Promise((_resolve, reject) => {
            signal = options.signal;
            signal.addEventListener('abort', () =>
              reject(new DOMException('Aborted', 'AbortError')),
            );
          }),
      ),
    );
    const controller = new AbortController();
    const result = expect(
      api('/api/v1/applications', {
        signal: controller.signal,
      }),
    ).rejects.toThrow('Aborted');
    controller.abort();
    await result;
    expect(signal.aborted).toBe(true);
  });
});
