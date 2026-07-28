import { MOCK_DEBRIS_FIELD, MOCK_PLAN, MOCK_NAIVE_ROUTE, mockReplan } from './mockData.js';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';
const TIMEOUT_MS = 2500;

async function tryFetch(path, options) {
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`${API_BASE}${path}`, { ...options, signal: controller.signal });
    clearTimeout(t);
    if (!res.ok) throw new Error(`${res.status}`);
    return await res.json();
  } catch (err) {
    clearTimeout(t);
    return null; // signals "use mock" to callers below
  }
}

export async function fetchDebrisField() {
  const live = await tryFetch('/debris-field');
  return live ?? MOCK_DEBRIS_FIELD;
}

export async function fetchPlan(body) {
  const live = await tryFetch('/plan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return live ?? MOCK_PLAN;
}

export async function fetchNaiveRoute() {
  const live = await tryFetch('/naive-route');
  return live ?? MOCK_NAIVE_ROUTE;
}

export async function fetchReplan(currentPlan, userRequestText, planParams) {
  const live = await tryFetch('/replan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...planParams, user_request_text: userRequestText }),
  });
  return live ?? mockReplan(currentPlan, userRequestText);
}
