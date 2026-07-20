export type SafeResponse = { ok: boolean; status: number; data: any; error: string | null };

export const BACKEND = (process.env.NEXT_PUBLIC_BACKEND_URL || '').replace(/\/+$/, '');
export const SEARCH_BACKEND = (process.env.NEXT_PUBLIC_SEARCH_BACKEND_URL || process.env.NEXT_PUBLIC_WORKER_BACKEND_URL || process.env.NEXT_PUBLIC_BACKEND_URL || '').replace(/\/+$/, '');
export const STORY_BACKEND = (process.env.NEXT_PUBLIC_STORY_BACKEND_URL || process.env.NEXT_PUBLIC_AI_BACKEND_URL || process.env.NEXT_PUBLIC_SEARCH_BACKEND_URL || process.env.NEXT_PUBLIC_WORKER_BACKEND_URL || process.env.NEXT_PUBLIC_BACKEND_URL || '').replace(/\/+$/, '');
export const BACKEND_CONFIG_ERROR = 'Core backend URL is not configured. Add NEXT_PUBLIC_BACKEND_URL in Vercel Settings → Environment Variables, then redeploy.';
export const SEARCH_BACKEND_CONFIG_ERROR = 'Search worker URL is not configured. Add NEXT_PUBLIC_SEARCH_BACKEND_URL in Vercel Settings → Environment Variables, then redeploy.';
export const STORY_BACKEND_CONFIG_ERROR = 'Story/AI worker URL is not configured. Add NEXT_PUBLIC_STORY_BACKEND_URL or NEXT_PUBLIC_SEARCH_BACKEND_URL in Vercel Settings → Environment Variables, then redeploy.';

// This is an internal-demo convenience header only. NEXT_PUBLIC_* values are visible
// in browser bundles, so this is not a substitute for SSO/auth proxy protection.
export const PUBLIC_MUTATION_TOKEN = process.env.NEXT_PUBLIC_DFP2_ADMIN_TOKEN || '';

export function backendHeaders(init?: HeadersInit, method = 'GET'): Headers {
  const headers = new Headers(init || {});
  if (PUBLIC_MUTATION_TOKEN && method.toUpperCase() !== 'GET') {
    headers.set('X-DFP2-ADMIN-TOKEN', PUBLIC_MUTATION_TOKEN);
  }
  return headers;
}

function serviceUrl(base: string, url: string): string {
  if (/^https?:\/\//i.test(url)) return url;
  return `${base}/${url.replace(/^\/+/, '')}`;
}

async function safeServiceJSON(
  base: string,
  configError: string,
  url: string,
  opts?: RequestInit,
): Promise<SafeResponse> {
  if (!base) return { ok: false, status: 0, data: null, error: configError };

  const method = (opts?.method || 'GET').toUpperCase();
  const headers = backendHeaders(opts?.headers, method);

  try {
    const res = await fetch(serviceUrl(base, url), { ...opts, headers });
    const text = await res.text();
    let data: any = null;

    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      return {
        ok: false,
        status: res.status,
        data: null,
        error: 'Server did not return JSON' + (text ? ' — ' + text.slice(0, 120) : ''),
      };
    }

    return {
      ok: res.ok,
      status: res.status,
      data,
      error: res.ok ? null : (data?.error || data?.detail || `Server error ${res.status}`),
    };
  } catch (err: any) {
    return {
      ok: false,
      status: 0,
      data: null,
      error: 'Could not reach the server — ' + (err?.message || 'network error'),
    };
  }
}

/** Call the core backend safely. `url` may be absolute or a service-relative path. */
export async function safeJSON(url: string, opts?: RequestInit): Promise<SafeResponse> {
  return safeServiceJSON(BACKEND, BACKEND_CONFIG_ERROR, url, opts);
}

/** Call the search worker safely. `url` may be absolute or a service-relative path. */
export async function safeSearchJSON(url: string, opts?: RequestInit): Promise<SafeResponse> {
  return safeServiceJSON(SEARCH_BACKEND, SEARCH_BACKEND_CONFIG_ERROR, url, opts);
}

/** Call the story/AI worker safely. `url` may be absolute or a service-relative path. */
export async function safeStoryJSON(url: string, opts?: RequestInit): Promise<SafeResponse> {
  return safeServiceJSON(STORY_BACKEND, STORY_BACKEND_CONFIG_ERROR, url, opts);
}

export async function backendFetch(url: string, opts?: RequestInit): Promise<Response> {
  const method = (opts?.method || 'GET').toUpperCase();
  const headers = backendHeaders(opts?.headers, method);
  return fetch(serviceUrl(BACKEND, url), { ...opts, headers });
}

export function isTerminalReady(data: any) {
  const stage = String(data?.stage || '').toLowerCase();
  const runStatus = String(data?.run_status || data?.process_state || '').toLowerCase();
  return ['results_ready', 'partial_results_ready', 'completed', 'complete', 'done', 'finished'].includes(stage)
    || ['completed', 'complete', 'done', 'finished', 'success'].includes(runStatus);
}

export function isFailureStatus(data: any) {
  const s = String(data?.run_status || data?.process_state || data?.stage || '').toLowerCase();
  return ['error', 'failed', 'cancelled', 'canceled'].includes(s);
}
