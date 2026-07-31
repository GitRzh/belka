// Thin wrapper around the backend documented in CHECKPOINT.txt's
// "API SURFACE FOR FRONTEND" section. Keep response shapes untouched here —
// let the components decide what to do with explanation_error / warning / etc.

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

async function request(path, options = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })

  // Don't throw on 404/422 — callers need the body (hint messages, field
  // errors) per the "Error/edge states" notes in CHECKPOINT.txt.
  let body = null
  try {
    body = await res.json()
  } catch {
    // no body / not JSON
  }

  if (!res.ok) {
    const err = new Error(body?.detail || `Request failed: ${res.status}`)
    err.status = res.status
    err.body = body
    throw err
  }

  return body
}

export const api = {
  getLaunchSites: () => request('/launch-sites'),

  getDebrisField: () => request('/debris-field'),

  getDebrisById: (noradId) => request(`/debris/${noradId}`),

  getNaiveRoute: (params) => {
    const qs = new URLSearchParams({
      start_altitude_km: params.start_altitude_km,
      start_inclination_deg: params.start_inclination_deg,
      fuel_budget_km_s: params.fuel_budget_km_s,
      // start_raan_deg is optional in PlanForm (left blank → omitted from
      // lastPlanRequest payload). Only send it when present so the backend
      // falls back to its own default (0.0) rather than receiving "undefined".
      ...(params.start_raan_deg != null ? { start_raan_deg: params.start_raan_deg } : {}),
      ...(params.pool_size != null ? { pool_size: params.pool_size } : {}),
    }).toString()
    return request(`/naive-route?${qs}`)
  },

  plan: (payload) =>
    request('/plan', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  replan: (payload) =>
    request('/replan', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}
