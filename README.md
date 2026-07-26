Still being made. Hold on.

## Explainability

Every recommendation this system makes traces to a rule, not a black-box guess:

- **Removal method** (`net_capture`, `monitor_only`, `robotic_arm_or_net_capture`) comes from a deterministic lookup table in `removal_method.py` — fragment vs. intact status from the object's name, split further by a bstar threshold. No LLM involvement in this classification.
- **Risk score** comes from an explicit, inspectable formula (`risk_score.py`): a weighted blend of local congestion (`proximity_score`) and estimated residual orbital lifetime from drag (`lifetime_score`).
- **Mission briefings and replan diffs** are the only LLM-generated text in the system, and they narrate data that was already computed deterministically upstream — the LLM explains the plan, it doesn't decide the plan.

`risk_score` specifically should be read as a **relative ranking within a given scored batch**, not an absolute collision probability or a NASA-standard conjunction metric — it prioritizes which objects to target first, not the odds of any specific collision.

## Real-World Grounding

This is a planning/decision-support tool, not a physical debris-removal simulation. It's built against real constraints and real missions currently flying:

- NASA Technical Standard 8719.14 requires all NASA flight projects to include debris assessments and end-of-mission disposal planning.
- NASA's Orbital Debris Program Office has found that removing the ~5 highest-risk objects per year can stabilize the LEO debris environment — smart targeting matters more than raw removal volume, which is the core premise behind this project's risk-ranked optimizer rather than a naive nearest-neighbor route.
- Active/tested ADR missions this project is grounded against: RemoveDEBRIS (ESA/Surrey — net and harpoon capture demonstrations on CubeSat targets, completed in orbit 2018-2019), Astroscale's ELSA-d (magnetic capture demonstration, successfully completed and de-orbited in January 2024), and ClearSpace-1 (ESA/ClearSpace — in development, timeline and target details have shifted since the mission was first announced, not yet flown). These validate that net-capture and robotic-arm/magnetic-capture are real, flown or in-development removal method categories — the same two families this project's `removal_method.py` classifies against.
- We have not been able to independently verify specific published delta-v/fuel budgets for these missions from primary sources, so no ADR-mission-specific numeric comparison is included here — stated as a known limitation rather than an estimated one.
- What is verified: the delta-v model itself (`delta_v.py`) is checked against well-documented, textbook orbital mechanics benchmarks, not just trusted because it runs:

  | Check | This project's output | Commonly cited reference |
  |---|---|---|
  | LEO(300km) → GEO Hohmann transfer, total | 3.893 km/s | ~3.90 km/s |
  | Pure 90° plane change at LEO circular velocity | 10.926 km/s | ~10.9–11.0 km/s |
  | GTO→GEO combined burn, Cape Canaveral (28.5°) | 1.837 km/s | ~1.80 km/s |
  | GTO→GEO combined burn, Kourou (~6°) | 1.497 km/s | ~1.46 km/s |

  The last two rows also reproduce the well-known ~0.3–0.35 km/s cost gap between equatorial and non-equatorial launch sites for GEO insertion — the real reason equatorial spaceports are preferred for GEO missions. This is what grounds the optimizer's cost matrix: it isn't fitted to debris-removal missions specifically, but it is validated against real, independently checkable orbital mechanics.
- `net_capture` assumes a `nets_carried` limit (default 1, matching RemoveDEBRIS's actual flight history — it carried exactly one net) unless the user explicitly raises it for an exploratory what-if run.
- `robotic_arm_or_net_capture`'s reusability assumption (grapple, release, move to the next target, repeat) has no flown precedent on *uncooperative* debris — the only real reusable-capture mission design (Astroscale ELSA-M) only works on targets with a pre-installed magnetic docking plate, and none of this project's debris populations have one. This is a feasibility gap, not a capacity gap, and has no code fix — it's disclosed here rather than modeled.
- No collision/conjunction screening of planned transfer trajectories against other tracked objects is performed. Real missions handle this via ground-based Conjunction Data Message (CDM) screening as a separate operational step before any maneuver executes, not baked into planning software.