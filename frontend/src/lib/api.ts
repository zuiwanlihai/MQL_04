import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios';
import type { Session } from '@/types/api';

/**
 * Shared axios client.
 *
 * - baseURL is `/api/v1` (vite dev server proxies /api → localhost:8000).
 * - Tokens live in localStorage (module-local helpers — do NOT import the
 *   auth store here, that would create a circular dependency).
 * - On 401 the interceptor performs a single-flight refresh and replays the
 *   original request; concurrent 401s share one refresh promise.
 */

const ACCESS_KEY = 'mt5quant.access_token';
const REFRESH_KEY = 'mt5quant.refresh_token';

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_KEY);
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY);
}

export function setTokens(access: string, refresh: string): void {
  localStorage.setItem(ACCESS_KEY, access);
  localStorage.setItem(REFRESH_KEY, refresh);
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

export const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
});

// Attach the bearer token to every request (auth endpoints included — harmless).
api.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Endpoints that must NOT trigger a refresh on 401.
const AUTH_PATHS = ['/auth/login', '/auth/register', '/auth/refresh', '/auth/logout'];

let refreshPromise: Promise<string> | null = null;

export async function refreshAccessToken(): Promise<string> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    throw new Error('No refresh token');
  }
  // Use a bare instance: no interceptor recursion, no auth header needed.
  const { data } = await axios.post<Session>('/api/v1/auth/refresh', {
    refresh_token: refreshToken,
  });
  setTokens(data.access_token, data.refresh_token);
  return data.access_token;
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as
      | (InternalAxiosRequestConfig & { _retry?: boolean })
      | undefined;

    const isAuthPath =
      original?.url != null && AUTH_PATHS.some((p) => original.url!.startsWith(p));

    if (
      error.response?.status === 401 &&
      original &&
      !original._retry &&
      !isAuthPath &&
      getRefreshToken()
    ) {
      original._retry = true;
      try {
        if (!refreshPromise) {
          refreshPromise = refreshAccessToken().finally(() => {
            refreshPromise = null;
          });
        }
        const token = await refreshPromise;
        original.headers.Authorization = `Bearer ${token}`;
        return api(original);
      } catch {
        clearTokens();
        if (window.location.pathname !== '/login') {
          window.location.href = '/login';
        }
      }
    }
    return Promise.reject(error);
  },
);

/** Backend detail messages mapped to Chinese for display. */
const DETAIL_TRANSLATIONS: Record<string, string> = {
  'Email already registered': '该邮箱已被注册',
  'Username already taken': '该用户名已被占用',
  'Invalid email or password': '邮箱或密码错误',
  'Account disabled': '账号已被禁用',
  'Invalid or expired session': '会话已失效，请重新登录',
  'Could not validate credentials': '登录凭证无效，请重新登录',
  'No refresh token': '登录已过期，请重新登录',
  'Field required': '该字段为必填项',
};

function translate(message: string): string {
  return DETAIL_TRANSLATIONS[message] ?? message;
}

/** Normalize any API error to a user-facing message. */
export function apiError(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail;
    if (typeof detail === 'string') return translate(detail);
    if (Array.isArray(detail)) {
      return detail
        .map((d: { msg?: string }) => d.msg)
        .filter((m): m is string => Boolean(m))
        .map(translate)
        .join('；');
    }
    if (!err.response) return '网络错误，请检查后端服务是否运行';
    return '请求失败，请稍后重试';
  }
  if (err instanceof Error) return translate(err.message);
  return '请求失败，请稍后重试';
}
