import { useState, useEffect } from 'react'
import { Viewer, Entity, PolylineGraphics, PointGraphics } from 'resium'
import { Cartesian3, Color, PolylineDashMaterialProperty } from 'cesium'

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
  // riskScore assumed 0-1; red = high risk, blue = low. Adjust once we
  // know the real distribution from a live /debris-field response.
  const r = Math.min(1, Math.max(0, riskScore))
  return Color.fromHsl(0.66 * (1 - r), 0.8, 0.5, 0.9)
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

export default function DebrisGlobe({ debrisField, route, depot, routeStyle = 'solid', cacheMetadata }) {
  const ageMin = useCacheAge(cacheMetadata?.data_fetched_at)

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
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
    {ageMin !== null && (
      <div style={{
        position: 'absolute', bottom: 8, left: 8, zIndex: 10,
        background: 'rgba(0,0,0,0.55)', color: '#e0e0e0',
        fontSize: 12, padding: '3px 8px', borderRadius: 4,
        display: 'flex', alignItems: 'center', gap: 6,
      }}>
        {`Debris data: ${ageMin} min old`}
        {cacheMetadata?.data_stale && (
          <span style={{
            background: '#c17f24', color: '#fff',
            fontSize: 10, padding: '1px 5px', borderRadius: 3,
          }}>refreshing soon</span>
        )}
      </div>
    )}
    {/* `full` makes resium size the Viewer to the whole browser window,
        ignoring our flex layout and covering the sidebar. Size it to the
        parent div instead so the PlanForm/ReasoningPanel column stays visible. */}
    <Viewer
      timeline={false}
      animation={false}
      style={{ width: '100%', height: '100%' }}
    >
      {/* Depot marker: distinct cyan dot so it's visually separate from the
          risk-colored debris dots. Rendered only when a plan/depot exists. */}
      {depot && (
        <Entity key="depot" position={debrisPosition(depot)} name="Depot (spacecraft start)">
          <PointGraphics
            pixelSize={14}
            color={Color.CYAN.withAlpha(0.95)}
          />
        </Entity>
      )}

      {debrisField.map((debris) => (
        <Entity key={debris.norad_id} position={debrisPosition(debris)} name={debris.name}>
          <PointGraphics
            pixelSize={
              visitedIds && visitedIds.has(debris.norad_id)
                ? riskSize(debris.risk_score) + 2
                : riskSize(debris.risk_score)
            }
            color={
              visitedIds && !visitedIds.has(debris.norad_id)
                ? riskColor(debris.risk_score).withAlpha(0.3)
                : riskColor(debris.risk_score)
            }
          />
        </Entity>
      ))}

      {routePositions && (
        <Entity>
          <PolylineGraphics
            positions={routePositions}
            width={routeStyle === 'solid' ? 3 : 2}
            material={
              routeStyle === 'solid'
                ? Color.WHITE
                : new PolylineDashMaterialProperty({ color: Color.YELLOW.withAlpha(0.8), dashLength: 16 })
            }
          />
        </Entity>
      )}
    </Viewer>
    </div>
  )
}