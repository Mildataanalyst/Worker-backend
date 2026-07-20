import { Redis } from '@upstash/redis';
import { DEFAULT_DASHBOARD_DATA } from './progressData';
import { backendServerFetch } from './backendServerClient';

export const DASHBOARD_KEY = 'dashboard:data';

function withWarning(message: string) {
  return { ...DEFAULT_DASHBOARD_DATA, _dashboardWarning: message };
}

function backendBase() {
  return (process.env.BACKEND_URL || process.env.NEXT_PUBLIC_BACKEND_URL || '').replace(/\/+$/, '');
}

function getRedis() {
  const url = process.env.UPSTASH_REDIS_REST_URL || process.env.KV_REST_API_URL;
  const token = process.env.UPSTASH_REDIS_REST_TOKEN || process.env.KV_REST_API_TOKEN;

  if (!url || !token) return null;
  return new Redis({ url, token });
}

async function getDashboardFromBackend() {
  const base = backendBase();
  if (!base) return null;
  try {
    const res = await fetch(`${base}/dashboard`, { cache: 'no-store' });
    const json = await res.json().catch(() => null);
    if (res.ok && json?.ok && json?.data) return json.data;
    return withWarning(json?.error || 'Progress backend returned an invalid dashboard response.');
  } catch (err) {
    console.error('Dashboard backend read failed:', err);
    return withWarning('Progress backend could not be reached. Showing default dashboard data.');
  }
}

export async function getDashboardData() {
  const backendData = await getDashboardFromBackend();
  if (backendData) return backendData;

  const redis = getRedis();
  if (!redis) {
    return withWarning('Progress storage is not connected yet. Set NEXT_PUBLIC_BACKEND_URL/BACKEND_URL for Render backend storage, or configure Upstash Redis.');
  }

  try {
    const data = await redis.get(DASHBOARD_KEY);
    return data || DEFAULT_DASHBOARD_DATA;
  } catch (err) {
    console.error('Dashboard Redis read failed:', err);
    return withWarning('Progress storage could not be reached. Showing default dashboard data.');
  }
}

export async function setDashboardData(data: any, password?: string) {
  const base = backendBase();
  if (base) {
    const res = await backendServerFetch(`${base}/dashboard/update`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password, data }),
      cache: 'no-store',
    });
    const json = await res.json().catch(() => null);
    if (!res.ok || !json?.ok) throw new Error(json?.error || `Progress backend update failed (${res.status}).`);
    return json.data;
  }

  const redis = getRedis();
  if (!redis) {
    throw new Error('Progress storage is not connected. Add NEXT_PUBLIC_BACKEND_URL/BACKEND_URL for Render backend storage, or add UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN in Vercel before publishing.');
  }
  await redis.set(DASHBOARD_KEY, data);
  return data;
}
