import { useState, useEffect, useRef } from 'react'
import { Viewer, Entity, PolylineGraphics, PointGraphics } from 'resium'
import { Cartesian3, Color, Credit, PolylineDashMaterialProperty, ScreenSpaceEventType } from 'cesium'

// Spherical linear interpolation between two Cartesian3 positions.
// Returns `steps` points from `a` up to (but not including) `b`.
// Each point's radius is linearly interpolated between rA and rB so that:
//   - t=0 lands exactly on `a` (not on the mean-altitude shell)
//   - intermediate points track the true orbital altitude gradient
//   - junction points between consecutive legs align with the debris position
// The caller appends `b` once at the end of the full arc chain.
const ARC_STEPS = 10
function slerpArc(a, b, steps = ARC_STEPS) {
  const rA = Cartesian3.magnitude(a)
  const rB = Cartesian3.magnitude(b)

  // Unit vectors along each endpoint direction.
  const uA = Cartesian3.normalize(a, new Cartesian3())
  const uB = Cartesian3.normalize(b, new Cartesian3())

  // Angle between the two unit vectors (clamped to avoid NaN from fp drift).
  const dot = Math.min(1, Math.max(-1, Cartesian3.dot(uA, uB)))
  const omega = Math.acos(dot)

  // Use lerp fallback when points are effectively coincident (omega ≈ 0) OR
  // antipodal (omega ≈ π) — both cases make sinOmega → 0 and would divide
  // the slerp scale factors by near-zero, blowing up or producing NaN.
  const NEAR_ZERO = 1e-10
  const useLinear = omega < NEAR_ZERO || omega > Math.PI - NEAR_ZERO

  const points = []
  for (let i = 0; i < steps; i++) {
    const t = i / steps
    let dir
    if (useLinear) {
      // Linear blend of unit vectors; normalise to get direction.
      dir = Cartesian3.lerp(uA, uB, t, new Cartesian3())
    } else {
      // Classic slerp: sin((1-t)ω)/sin(ω)·uA + sin(tω)/sin(ω)·uB
      const sinOmega = Math.sin(omega)
      const scaleA = Math.sin((1 - t) * omega) / sinOmega
      const scaleB = Math.sin(t * omega) / sinOmega
      const scaledA = Cartesian3.multiplyByScalar(uA, scaleA, new Cartesian3())
      const scaledB = Cartesian3.multiplyByScalar(uB, scaleB, new Cartesian3())
      dir = Cartesian3.add(scaledA, scaledB, new Cartesian3())
    }
    // Interpolate radius linearly between rA and rB so t=0 lands exactly on
    // `a` (not on the mean-altitude shell), keeping junction points accurate.
    Cartesian3.normalize(dir, dir)
    const r = rA + t * (rB - rA)
    points.push(Cartesian3.multiplyByScalar(dir, r, new Cartesian3()))
  }
  return points
}

// Unstyled/functional per Week 5 plan — polish comes later.
// Debris dots are colored/sized by risk_score so the "risk-ranked" framing
// from PLAN.txt is visible at a glance, not just implied by list order.

function riskColor(riskScore) {
  // Green (hue 120°) → yellow (60°) → red (0°) as risk rises.
  // Two linear segments: low half shifts green→yellow, high half yellow→red.
  const r = Math.min(1, Math.max(0, riskScore))
  const hue = r <= 0.5
    ? (120 - r * 2 * 60) / 360        // 120°→60° over [0, 0.5]
    : (60  - (r - 0.5) * 2 * 60) / 360  // 60°→0° over [0.5, 1]
  return Color.fromHsl(hue, 1, 0.5, 0.9)
}

function riskSize(riskScore) {
  const r = Math.min(1, Math.max(0, riskScore))
  return 6 + r * 10
}

// altitude_km + latitude/longitude -> Cesium cartesian position.
function debrisPosition(debris) {
  return Cartesian3.fromDegrees(
    debris.longitude,
    debris.latitude,
    debris.altitude_km * 1000, // km -> m
  )
}

// Backend's `route` field is list[str] of labels like "COSMOS 2251 (22675)",
// not norad_ids directly -- see main.py's _norad_ids_from_plan(), which
// regexes the trailing (\d+) out of each label the same way. Depot label
// has no parens/id ("DEPOT (spacecraft start)" is an exception -- it DOES
// have parens but no plain trailing digits, so this regex correctly misses
// it too), and comes back as null here, filtered out below.
function noradIdFromRouteLabel(label) {
  const m = /\((\d+)\)$/.exec(label)
  return m ? Number(m[1]) : null
}

function useCacheAge(dataFetchedAt) {
  const [ageMin, setAgeMin] = useState(null)

  useEffect(() => {
    if (!dataFetchedAt) return
    function update() {
      const diffMs = Date.now() - new Date(dataFetchedAt).getTime()
      setAgeMin(Math.floor(diffMs / 60000))
    }
    update()
    const id = setInterval(update, 30000)
    return () => clearInterval(id)
  }, [dataFetchedAt])

  return ageMin
}

export default function DebrisGlobe({
  debrisField,
  route,
  depot,
  routeStyle = 'solid',
  cacheMetadata,
  focusMode = 'dim',
  activeDebrisId = null,
  pinnedIds = null,        // Set<noradId> of pinned (but not minimized) objects
  customSelecting = false,
  customSelectedIds = null,
  // customFilterConfig: { minRisk: number, methods: string[] } | null
  // Applied as visual dimming inside custom selection mode.
  // null = no filter active. An object passes when:
  //   - risk_score >= minRisk  (minRisk 0 = no risk gate)
  //   - methods is empty OR removal_method is in methods  (AND logic)
  // Dimmed objects remain fully clickable/selectable — this is visual only.
  customFilterConfig = null,
  onDebrisSelect,
  onDebrisToggleSelect,
  onBackgroundClick,
}) {
  const viewerRef = useRef(null)
  const ageMin = useCacheAge(cacheMetadata?.data_fetched_at)

  // Wire Cesium's ScreenSpaceEventHandler for left-click on the globe.
  // We use the native Cesium API rather than resium's onClick to reliably
  // distinguish "clicked an entity" from "clicked empty space".
  useEffect(() => {
    const viewer = viewerRef.current?.cesiumElement
    if (!viewer) return

    const handler = viewer.screenSpaceEventHandler
    function handleClick(movement) {
      const picked = viewer.scene.pick(movement.position)
      if (picked?.id) {
        // Cesium Entity was clicked — find matching debris object by norad_id.
        // Entity `name` is set to debris.name; the entity `id` is the React
        // <Entity key={norad_id}> which Cesium stores as the entity id string.
        const clickedId = Number(picked.id.id ?? picked.id)
        const debris = debrisField.find((d) => d.norad_id === clickedId)
        if (debris) {
          if (customSelecting && onDebrisToggleSelect) {
            // Custom selection mode: toggle membership, suppress the info modal path
            onDebrisToggleSelect(debris)
          } else if (onDebrisSelect) {
            onDebrisSelect(debris)
          }
        }
      } else {
        // Clicked empty globe background
        if (onBackgroundClick) onBackgroundClick()
      }
    }

    handler.setInputAction(handleClick, ScreenSpaceEventType.LEFT_CLICK)
    return () => {
      // Clean up: reset to Cesium's default no-op rather than removing the
      // whole handler (other Cesium internals may still need it intact).
      handler.removeInputAction(ScreenSpaceEventType.LEFT_CLICK)
    }
  }, [debrisField, customSelecting, onDebrisSelect, onDebrisToggleSelect, onBackgroundClick])

  // Remove the default "Powered by Cesium" watermark from the bottom-left.
  // Called via <Viewer onReady> so the cesiumElement is guaranteed non-null
  // (the useEffect approach fails because cesiumElement is null at mount time).
  function handleViewerReady(viewer) {
    viewer.creditDisplay.removeStaticCredit(Credit.CESIUM_CREDIT)
  }

  // Resolve each route label to a Cesium position.  Depot is prepended so the
  // depot -> first-debris leg actually draws; without it the polyline only
  // connects debris-to-debris and the first leg is missing.
  const stopPositions = route?.length
    ? [
        ...(depot ? [debrisPosition(depot)] : []),
        ...route
          .map(noradIdFromRouteLabel)
          .map((noradId) => debrisField.find((d) => d.norad_id === noradId))
          .filter(Boolean)
          .map(debrisPosition),
      ]
    : null

  // Expand consecutive stop pairs into smooth arcs via slerp.
  // Each leg contributes ARC_STEPS intermediate points; the final stop is
  // appended once at the end so no position is duplicated.
  const routePositions = stopPositions?.length >= 2
    ? [
        ...stopPositions.slice(0, -1).flatMap((pos, i) =>
          slerpArc(pos, stopPositions[i + 1])
        ),
        stopPositions[stopPositions.length - 1],
      ]
    : stopPositions

  // Built once per render from the same noradIdFromRouteLabel() parse used
  // above. null (not an empty Set) when there's no route, so the visitedIds
  // ternaries below can distinguish "no route yet" from "route exists but
  // this object wasn't visited" -- the former keeps full opacity everywhere.
  const visitedIds = route?.length
    ? new Set(route.map(noradIdFromRouteLabel).filter((id) => id !== null))
    : null

  return (
    <div className="globe-viewport">
    {ageMin !== null && (
      <div className={`cache-chip${cacheMetadata?.data_stale ? ' cache-chip--stale' : ''}`}>
        {`Data: ${ageMin} min old`}
        {cacheMetadata?.data_stale && (
          <span className="stale-tag">refreshing soon</span>
        )}
      </div>
    )}
    {/* `full` makes resium size the Viewer to the whole browser window,
        ignoring our flex layout and covering the sidebar. Size it to the
        parent div instead so the PlanForm/ReasoningPanel column stays visible. */}
    <Viewer
      ref={viewerRef}
      timeline={false}
      animation={false}
      style={{ width: '100%', height: '100%' }}
      onReady={handleViewerReady}
    >
      {/* Depot marker: distinct cyan dot so it's visually separate from the
          risk-colored debris dots. Rendered only when a plan/depot exists. */}
      {depot && (
        <Entity key="depot" position={debrisPosition(depot)} name="Depot (spacecraft start)">
          <PointGraphics
            pixelSize={16}
            color={Color.WHITE}
            outlineColor={Color.BLACK}
            outlineWidth={2}
          />
        </Entity>
      )}

      {debrisField
        .filter((debris) =>
          // 'focus': skip non-visited entities entirely (reduces Cesium entity count).
          // Pass through everything when focusMode !== 'focus', or when there's no
          // active route (visitedIds === null), or when this dot is on the route.
          focusMode !== 'focus' || !visitedIds || visitedIds.has(debris.norad_id)
        )
        .map((debris) => {
          const isVisited = visitedIds && visitedIds.has(debris.norad_id)

          // --- Debris-selection dimming (takes priority over route focus mode) ---
          // When a debris is selected: selected dot = full brightness, boosted size;
          // all others = 0.15 alpha (strong dim so the selected dot pops clearly).
          // When nothing is selected: fall back to the existing focusMode logic.
          let color
          let pixelSize = isVisited ? riskSize(debris.risk_score) + 2 : riskSize(debris.risk_score)

          const isPinned  = pinnedIds?.has(debris.norad_id) ?? false
          const isActive  = debris.norad_id === activeDebrisId

          if (customSelecting && customSelectedIds) {
            // Custom selection mode: selected dots get cyan highlight + size boost.
            // Unselected dots dim only when a filter is actively set (minRisk > 0 or
            // methods selected). No filter → full opacity for all.
            const isSelected = customSelectedIds.has(debris.norad_id)
            const filterActive =
              customFilterConfig &&
              (customFilterConfig.minRisk > 0 || customFilterConfig.methods.length > 0)

            let passesFilter = true
            if (filterActive) {
              // AND logic: must satisfy both risk and method axes
              const riskOk = debris.risk_score >= customFilterConfig.minRisk
              const methodOk =
                customFilterConfig.methods.length === 0 ||
                customFilterConfig.methods.includes(debris.removal_method)
              passesFilter = riskOk && methodOk
            }

            if (isSelected) {
              color = Color.fromCssColorString('#00e5ff').withAlpha(0.95)
              pixelSize = riskSize(debris.risk_score) + 6
            } else if (filterActive && !passesFilter) {
              color = riskColor(debris.risk_score).withAlpha(0.3)
            } else {
              color = riskColor(debris.risk_score)
            }
          } else if (activeDebrisId !== null || (pinnedIds && pinnedIds.size > 0)) {
            if (isActive) {
              // Active entity: full brightness + size boost
              color = riskColor(debris.risk_score)
              pixelSize = riskSize(debris.risk_score) + 4
            } else if (isPinned) {
              // Pinned entity: full brightness, slightly larger (globe highlight persists)
              color = riskColor(debris.risk_score)
              pixelSize = riskSize(debris.risk_score) + 2
            } else {
              // All other entities: dimmed
              color = riskColor(debris.risk_score).withAlpha(0.15)
            }
          } else {
            // No active debris selection and not in custom-select mode.
            // focusMode controls visibility (focus=hide non-route) but NOT opacity —
            // all rendered dots stay at full opacity regardless of all/dim/focus mode.
            color = riskColor(debris.risk_score)
          }

          return (
            <Entity key={debris.norad_id} id={String(debris.norad_id)} position={debrisPosition(debris)} name={debris.name}>
              <PointGraphics
                pixelSize={pixelSize}
                color={color}
                // Pinned objects: white outline ring so they visually stand out
                // from the active dot even when both are full-brightness risk color.
                outlineColor={isPinned ? Color.WHITE : undefined}
                outlineWidth={isPinned ? 2 : 0}
              />
            </Entity>
          )
        })}

      {routePositions && (
        <Entity>
          <PolylineGraphics
            positions={routePositions}
            width={routeStyle === 'solid' ? 3 : 2}
            material={
              routeStyle === 'solid'
                ? Color.WHITE
                : new PolylineDashMaterialProperty({ color: Color.fromCssColorString('#8A8A8E').withAlpha(0.85), dashLength: 16 })
            }
          />
        </Entity>
      )}
    </Viewer>
    </div>
  )
}