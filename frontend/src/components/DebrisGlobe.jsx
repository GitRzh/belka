import { useState, useEffect } from 'react'
import { Viewer, Entity, PolylineGraphics, PointGraphics } from 'resium'
import { Cartesian3, Color, PolylineDashMaterialProperty } from 'cesium'

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
  const routePositions = route?.length
    ? [
        ...(depot ? [debrisPosition(depot)] : []),
        ...route
          .map(noradIdFromRouteLabel)
          .map((noradId) => debrisField.find((d) => d.norad_id === noradId))
          .filter(Boolean)
          .map(debrisPosition),
      ]
    : null

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