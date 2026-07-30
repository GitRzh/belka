Still being made. Hold on.

## Explainability

Every recommendation this system makes traces to a rule, not a black-box guess:

- **Removal method** (`net_capture`, `monitor_only`, `robotic_arm_or_net_capture`) comes from a deterministic lookup table in `removal_method.py` — fragment vs. intact status from the object's name, split further by a bstar threshold. No LLM involvement in this classification.
- **Risk score** comes from an explicit, inspectable formula (`risk_score.py`): a weighted blend of local congestion (`proximity_score`), estimated residual orbital lifetime from drag (`lifetime_score`), and physical size (`size_score` — not available for every object, see [Data Sources & Quality](#data-sources--quality); handled explicitly rather than defaulted).
- **Mission briefings, replan diffs, and removal-method justifications** are the only LLM-generated text in the system, and they narrate data that was already computed deterministically upstream — the LLM explains the plan, it doesn't decide the plan. The removal-method justification is a 1-2 sentence explanation of why a given technique suits an object, generated once per technique (not per object — only 3 possible techniques exist, so the same text is reused across every object that shares one) and cached for the process lifetime. If the LLM call fails, a short deterministic template is shown instead so the field is never blank.

`risk_score` specifically should be read as a **relative ranking within a given scored batch**, not an absolute collision probability or a NASA-standard conjunction metric — it prioritizes which objects to target first, not the odds of any specific collision.

## Real-World Grounding

This is a planning/decision-support tool, not a physical debris-removal simulation. It's built against real constraints and real missions currently flying.

### Mission Precedent

- NASA Technical Standard 8719.14 requires all NASA flight projects to include debris assessments and end-of-mission disposal planning.
- NASA's Orbital Debris Program Office has found that removing the ~5 highest-risk objects per year can stabilize the LEO debris environment — smart targeting matters more than raw removal volume, which is the core premise behind this project's risk-ranked optimizer rather than a naive nearest-neighbor route.
- Active/tested ADR missions this project is grounded against: RemoveDEBRIS (ESA/Surrey — net and harpoon capture demonstrations on CubeSat targets, completed in orbit 2018-2019), Astroscale's ELSA-d (magnetic capture demonstration, successfully completed and de-orbited in January 2024), and ClearSpace-1 (ESA/ClearSpace — in development, timeline and target details have shifted since the mission was first announced, not yet flown). These validate that net-capture and robotic-arm/magnetic-capture are real, flown or in-development removal method categories — the same two families this project's `removal_method.py` classifies against.
- We have not been able to independently verify specific published delta-v/fuel budgets for these missions from primary sources, so no ADR-mission-specific numeric comparison is included here — stated as a known limitation rather than an estimated one.

### Orbital Mechanics Validation

The delta-v model itself (`delta_v.py`) is checked against well-documented, textbook orbital mechanics benchmarks, not just trusted because it runs:

| Check | This project's output | Commonly cited reference |
|---|---|---|
| LEO(300km) → GEO Hohmann transfer, total | 3.893 km/s | ~3.90 km/s |
| Pure 90° plane change at LEO circular velocity | 10.926 km/s | ~10.9–11.0 km/s |
| GTO→GEO combined burn, Cape Canaveral (28.5°) | 1.837 km/s | ~1.80 km/s |
| GTO→GEO combined burn, Kourou (~6°) | 1.497 km/s | ~1.46 km/s |

The last two rows also reproduce the well-known ~0.3–0.35 km/s cost gap between equatorial and non-equatorial launch sites for GEO insertion — the real reason equatorial spaceports are preferred for GEO missions. This is what grounds the optimizer's cost matrix: it isn't fitted to debris-removal missions specifically, but it is validated against real, independently checkable orbital mechanics.

### Data Sources & Quality

- `risk_score` includes a physical-size term alongside proximity and orbital lifetime (weights: proximity 0.45 / lifetime 0.30 / size 0.25), sourced from CelesTrak SATCAT's radar cross-section field (`rcs_m2`, m²) — larger objects pose a larger collision cross-section and produce more fragments on impact, which is why real debris-risk models weight by size. Coverage isn't 100%: on the last verified live run, 270 of 274 tracked objects had a catalogued RCS value; the 4 without one (all Iridium-33 fragments) are scored on proximity + lifetime only, renormalized — never defaulted to 0, which would have wrongly suppressed their risk. Coverage can vary between fetches since it depends on what SATCAT has on file at request time, not something this project controls.
- Every object carries a `data_quality` label (`fresh` / `aging` / `stale`) based on how many days old its TLE is relative to its own epoch — not how long ago this server fetched from Celestrak, which is a separate, already-short 2-hour cache cycle. Thresholds (fresh < 7 days, aging 7-14, stale > 14) come from published TLE-accuracy research: position error grows roughly 1-3 km/day from epoch, with ~2 weeks commonly cited as the outer edge of a reliable window. By default, route planning (`/plan`, `/replan`, `/naive-route`) excludes objects older than 14 days (`max_tle_age_days`, adjustable in either direction — raise it to include older/less-trusted debris, lower it to be stricter). `/debris-field` and `/debris/{norad_id}` never filter — they always show the full field with its quality label, so a user can see what's old before deciding anything. This can't make old tracking data fresh (that's a tracking-network limitation, not a bug); it only stops route planning from silently trusting it.
- The debris field is capped at 300 objects total (`MAX_OBJECTS` in `tle_fetch.py`, split evenly across the three tracked debris clouds so no single group can crowd out the others). This is a deliberate speed/demo tradeoff, not a data-completeness bug — raising it would slow the optimizer's solve time, which matters for a live demo more than covering every catalogued fragment.

### Removal-Method Assumptions

- `net_capture` assumes a `nets_carried` limit (default 1, matching RemoveDEBRIS's actual flight history — it carried exactly one net) unless the user explicitly raises it for an exploratory what-if run.
- `robotic_arm_or_net_capture`'s reusability assumption (grapple, release, move to the next target, repeat) has no flown precedent on *uncooperative* debris — the only real reusable-capture mission design (Astroscale ELSA-M) only works on targets with a pre-installed magnetic docking plate, and none of this project's debris populations have one. This is a feasibility gap, not a capacity gap, and has no code fix — it's disclosed here rather than modeled.

### Known Scope Limitations

- No collision/conjunction screening of planned transfer trajectories against other tracked objects is performed. Real missions handle this via ground-based Conjunction Data Message (CDM) screening as a separate operational step before any maneuver executes, not baked into planning software.
- Servicing multiple debris objects in different orbital planes cost-effectively is an open research problem, not something this project claims to solve — real ADR missions avoid it by targeting one object per mission rather than a multi-stop route. This project models multi-target routing anyway (that's the point of the optimizer), but the plane-change cost between distant targets is real and can dominate a route's fuel budget; it isn't a bug to fix, it's the actual reason single-target missions are the current operational norm.
- The route line drawn on the globe is a smooth spherical interpolation between two orbital positions (a great-circle-style arc with the correct altitude at each endpoint, not a straight chord) — it is still not a physically simulated transfer trajectory. It shows where the two endpoints are relative to each other, not the real burn/coast path a spacecraft would fly between them.