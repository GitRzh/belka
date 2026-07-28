// Shape matches CHECKPOINT.txt's "API SURFACE FOR FRONTEND" contract exactly,
// so swapping mock calls for real fetch calls in api.js is a no-op for every
// component below them.

export const MOCK_DEBRIS_FIELD = [
  { norad_id: 44201, name: "COSMOS 2251 DEB", altitude_km: 812, inclination_deg: 74.0, latitude: 12.4, longitude: -45.2, bstar: 0.00024, epoch_age_days: 3, proximity_score: 0.88, lifetime_score: 0.92, risk_score: 0.91, object_type: "fragment", removal_method: "net_capture", possible_methods: ["net_capture"], method_maturity: { net_capture: "flight_demonstrated" } },
  { norad_id: 39027, name: "FENGYUN 1C DEB", altitude_km: 855, inclination_deg: 98.6, latitude: -30.1, longitude: 60.7, bstar: 0.00019, epoch_age_days: 7, proximity_score: 0.79, lifetime_score: 0.90, risk_score: 0.84, object_type: "fragment", removal_method: "net_capture", possible_methods: ["net_capture"], method_maturity: { net_capture: "flight_demonstrated" } },
  { norad_id: 22626, name: "SL-16 R/B", altitude_km: 780, inclination_deg: 71.0, latitude: 40.2, longitude: 10.5, bstar: 0.00031, epoch_age_days: 2, proximity_score: 0.65, lifetime_score: 0.61, risk_score: 0.63, object_type: "intact", removal_method: "robotic_arm_or_net_capture", possible_methods: ["robotic_arm", "net_capture"], method_maturity: { robotic_arm: "conceptual", net_capture: "flight_demonstrated" } },
  { norad_id: 18421, name: "ARIANE 1 DEB", altitude_km: 900, inclination_deg: 82.5, latitude: -5.6, longitude: -120.3, bstar: 0.00011, epoch_age_days: 12, proximity_score: 0.58, lifetime_score: 0.55, risk_score: 0.57, object_type: "fragment", removal_method: "net_capture", possible_methods: ["net_capture"], method_maturity: { net_capture: "flight_demonstrated" } },
  { norad_id: 9044, name: "SL-8 R/B DEB", altitude_km: 950, inclination_deg: 65.8, latitude: 22.9, longitude: 140.1, bstar: 0.00006, epoch_age_days: 20, proximity_score: 0.40, lifetime_score: 0.43, risk_score: 0.41, object_type: "fragment", removal_method: "monitor_only", possible_methods: [], method_maturity: {} },
  { norad_id: 27386, name: "OKEAN-O DEB", altitude_km: 730, inclination_deg: 83.0, latitude: -55.2, longitude: 5.4, bstar: 0.00028, epoch_age_days: 5, proximity_score: 0.71, lifetime_score: 0.68, risk_score: 0.70, object_type: "fragment", removal_method: "net_capture", possible_methods: ["net_capture"], method_maturity: { net_capture: "flight_demonstrated" } },
];

function planFromSelection(field, selectedIds, { fuelBudget = 3.0 } = {}) {
  const route_details = selectedIds
    .map((id) => field.find((d) => d.norad_id === id))
    .filter(Boolean)
    .map((d) => ({
      norad_id: d.norad_id,
      name: d.name,
      object_type: d.object_type,
      removal_method: d.removal_method,
      possible_methods: d.possible_methods,
      method_maturity: d.method_maturity,
      risk_score: d.risk_score,
    }));
  const total_risk_collected = route_details.reduce((s, d) => s + d.risk_score, 0);
  return {
    route: selectedIds,
    route_details,
    visited_count: route_details.length,
    pool_size_used: field.length,
    net_capacity_constrained: false,
    total_fuel_cost_km_s: 2.1,
    fuel_budget_km_s: fuelBudget,
    fuel_used_fraction: 2.1 / fuelBudget,
    total_risk_collected,
    skipped_count: field.length - route_details.length,
    skipped_names: field.filter((d) => !selectedIds.includes(d.norad_id)).map((d) => d.name),
    step_breakdown: route_details.map((d, i) => `${i + 1}. ${d.name} — risk ${d.risk_score.toFixed(2)}`),
    explanation:
      "Route prioritizes 44201 and 39027 first: both carry the highest relative risk scores in this batch and sit within reach of the chosen launch site's inclination. 22626 is included as a net-capture-eligible intact object. Fuel budget allows five stops before the reserve margin is hit.",
    explanation_error: null,
    warning: null,
  };
}

export const MOCK_PLAN = planFromSelection(MOCK_DEBRIS_FIELD, [44201, 39027, 22626, 27386, 18421]);

export const MOCK_NAIVE_ROUTE = planFromSelection(MOCK_DEBRIS_FIELD, [22626, 27386, 18421, 44201, 39027]);
MOCK_NAIVE_ROUTE.explanation =
  "Nearest-neighbor baseline: visits whichever unvisited object is closest at each step, with no regard for risk score. Included for comparison against the optimized route.";

export function mockReplan(currentPlan, requestText) {
  const prioritizeRisk = /risk/i.test(requestText);
  const newSelection = prioritizeRisk
    ? [44201, 39027, 27386, 22626, 18421]
    : [22626, 18421, 27386, 44201, 39027];
  const new_plan = planFromSelection(MOCK_DEBRIS_FIELD, newSelection);
  return {
    old_plan: { route: currentPlan.route, route_details: currentPlan.route_details },
    new_plan,
    diff: {
      order_changed: JSON.stringify(currentPlan.route) !== JSON.stringify(new_plan.route),
      previous_order: currentPlan.route,
      new_order: new_plan.route,
    },
    explanation: prioritizeRisk
      ? "Re-ordered to front-load the two highest-risk objects, accepting a slightly higher fuel cost per stop in exchange for collecting more total risk earlier in the mission."
      : "Re-ordered toward the fuel-cheaper path first, deferring the highest-risk-but-costlier targets to later stops.",
    overrides_applied: prioritizeRisk ? { risk_penalty_scale: "increased" } : { fuel_priority: "increased" },
  };
}
