import type { JobRef } from './types';

declare global {
  interface Window {
    __APP_CONFIG__?: {
      API_BASE_UR?: string;
    };
  }
}
let csrfToken = '';
export function setCsrfToken(token: string) {
  csrfToken = token;
}
export function apiUrl(path: string): string {
  const configured = window.__APP_CONFIG__?.API_BASE_UR;
  if (typeof configured !== 'string') {
    throw new Error('缺少 API 配置，请检查 config.js。');
  }
  const root = configured.replace(/\/+$/, '');
  const url = new URL(root || window.location.origin);
  if (
    ![
      'http:',
      'https:',
    ].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  ) {
    throw new Error('config.js 中的 API 地址无效。');
  }
  return `${root}${path}`;
}
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public data?: JobRef,
  ) {
    super(message);
  }
}
export function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '请求失败，请稍后重试。';
}
interface ApiResponse<T> {
  data: T;
  error_message?: string;
}
function decode<T>(status: number, body: ApiResponse<T>): T {
  if (status < 200 || status >= 300) {
    if (status === 401) {
      window.dispatchEvent(new Event('session-expired'));
    }
    throw new ApiError(
      status,
      body.error_message || `请求失败 (${status})`,
      body.data as JobRef,
    );
  }
  return body.data;
}
export async function api<T>(
  path: string,
  options: RequestInit & {
    timeoutMs?: number;
  } = {},
): Promise<T> {
  const { timeoutMs = 30000, ...request } = options;
  const headers = new Headers(request.headers);
  if (request.body) {
    headers.set('Content-Type', 'application/json');
  }
  if (request.method && request.method !== 'GET') {
    headers.set('X-CSRF-Token', csrfToken);
  }
  const controller = new AbortController();
  const cancel = () => controller.abort();
  request.signal?.addEventListener('abort', cancel, {
    once: true,
  });
  if (request.signal?.aborted) {
    cancel();
  }
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    cancel();
  }, timeoutMs);
  try {
    const response = await fetch(apiUrl(path), {
      ...request,
      signal: controller.signal,
      headers,
      credentials: 'include',
    });
    let body: ApiResponse<T>;
    try {
      body = await response.json();
    } catch (error) {
      if (controller.signal.aborted) {
        throw error;
      }
      throw new Error('服务返回了无效响应，请检查 API 地址和服务状态。');
    }
    return decode<T>(response.status, body);
  } catch (error) {
    if (timedOut && !request.signal?.aborted) {
      throw new Error(
        '请求超时，已停止等待。服务端操作可能仍在执行，请稍后手动刷新。',
      );
    }
    throw error;
  } finally {
    clearTimeout(timer);
    request.signal?.removeEventListener('abort', cancel);
  }
}
export function upload(
  application: string,
  file: File,
  onProgress: (value: number) => void,
): Promise<JobRef> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', apiUrl(`/api/v1/applications/${application}/jobs`));
    xhr.withCredentials = true;
    xhr.timeout = 2 * 60 * 60 * 1000;
    xhr.setRequestHeader('X-CSRF-Token', csrfToken);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.upload.onload = () => onProgress(100);
    xhr.onload = () => {
      try {
        resolve(decode<JobRef>(xhr.status, JSON.parse(xhr.responseText)));
      } catch (error) {
        reject(error);
      }
    };
    xhr.onerror = () =>
      reject(
        new Error(
          '连接中断。任务可能仍在运行，请先查看签名历史，不要重复上传。',
        ),
      );
    xhr.onabort = () => reject(new Error('上传连接已关闭，请检查签名历史。'));
    xhr.ontimeout = () =>
      reject(
        new Error(
          '上传或签名等待超时。任务可能仍在运行，请先查看签名历史，不要重复上传。',
        ),
      );
    const data = new FormData();
    data.append('file', file);
    xhr.send(data);
  });
}
