function serverBackendToken() {
  return (process.env.DFP2_ADMIN_TOKEN || '').trim();
}

export function backendServerHeaders(init?: HeadersInit, method = 'GET'): Headers {
  const headers = new Headers(init || {});
  const token = serverBackendToken();
  if (token && method.toUpperCase() !== 'GET') {
    headers.set('X-DFP2-ADMIN-TOKEN', token);
  }
  return headers;
}

export async function backendServerFetch(url: string, opts?: RequestInit): Promise<Response> {
  const method = (opts?.method || 'GET').toUpperCase();
  const headers = backendServerHeaders(opts?.headers, method);
  return fetch(url, { ...opts, headers });
}
