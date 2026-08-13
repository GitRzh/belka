import { useState, useEffect, useRef, forwardRef, useImperativeHandle } from 'react'
import { Viewer, Entity, PolylineGraphics, PointGraphics } from 'resium'
import { Cartesian3, Color, Credit, PolylineDashMaterialProperty, ScreenSpaceEventType } from 'cesium'

// Interval step per tick (80 ms) — values 0→1 loop continuously.
const FLOW_TICK_MS = 80
const FLOW_STEP = 0.015

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

// Expose { flyTo(lon, lat, altKm), addPinEntity(id, lon, lat, altKm, type), removePinEntity(id) }
// to parent via ref so PlanForm pin buttons can drive the globe without prop-drilling through App.
const DebrisGlobe = forwardRef(function DebrisGlobe({
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
  // diffHighlightIds: Set<noradId> | null
  // Debris stops that changed relative to the previous route tab — highlighted cyan.
  diffHighlightIds = null,
  onDebrisSelect,
  onDebrisToggleSelect,
  onBackgroundClick,
}, ref) {
  const viewerRef = useRef(null)
  // Track pin entities keyed by caller-provided id so we can remove the old one on re-pin.
  const pinEntitiesRef = useRef({})
  const ageMin = useCacheAge(cacheMetadata?.data_fetched_at)

  // dashOffset cycles 0→1 for the animated flow overlay on the route line.
  const [dashOffset, setDashOffset] = useState(0)
  useEffect(() => {
    if (routeStyle !== 'solid') return
    const id = setInterval(() => {
      setDashOffset(prev => (prev + FLOW_STEP) % 1)
    }, FLOW_TICK_MS)
    return () => clearInterval(id)
  }, [routeStyle])

  useImperativeHandle(ref, () => ({
    /** Smoothly fly the camera to the given geographic position at the given range. */
    flyTo(lon, lat, altKm) {
      const viewer = viewerRef.current?.cesiumElement
      if (!viewer) return
      viewer.camera.flyTo({
        destination: Cartesian3.fromDegrees(lon, lat, altKm * 1000 + 2_000_000),
        duration: 1.5,
      })
    },

    /** Place a billboard pin entity at the given position, replacing any prior entity with same id. */
    addPinEntity(id, lon, lat, altKm, type = 'site') {
      const viewer = viewerRef.current?.cesiumElement
      if (!viewer) return
      // Remove prior entity for this id if present
      if (pinEntitiesRef.current[id]) {
        viewer.entities.remove(pinEntitiesRef.current[id])
      }
      // Inline SVG data-URI for house (site) and satellite icons.
      // Using data URIs keeps zero external dependencies.
      const svgHouse = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="28" height="28">
        <polygon points="12,2 2,10 4,10 4,22 10,22 10,15 14,15 14,22 20,22 20,10 22,10" fill="#f2f2f0" stroke="#000" stroke-width="1.2"/>
      </svg>`
      const svgSat = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="28" height="28">
        <circle cx="12" cy="12" r="3" fill="#f2f2f0"/>
        <line x1="12" y1="5" x2="12" y2="1" stroke="#f2f2f0" stroke-width="2"/>
        <line x1="12" y1="23" x2="12" y2="19" stroke="#f2f2f0" stroke-width="2"/>
        <line x1="5" y1="12" x2="1" y2="12" stroke="#f2f2f0" stroke-width="2"/>
        <line x1="23" y1="12" x2="19" y2="12" stroke="#f2f2f0" stroke-width="2"/>
        <rect x="3" y="10" width="6" height="4" fill="#3b82d4" stroke="#f2f2f0" stroke-width="0.8"/>
        <rect x="15" y="10" width="6" height="4" fill="#3b82d4" stroke="#f2f2f0" stroke-width="0.8"/>
      </svg>`
      const svgData = type === 'site' ? svgHouse : svgSat
      const dataUri = `data:image/svg+xml;base64,${btoa(svgData)}`

      const entity = viewer.entities.add({
        position: Cartesian3.fromDegrees(lon, lat, altKm * 1000),
        billboard: {
          image: dataUri,
          width: 28,
          height: 28,
          verticalOrigin: 1, // BOTTOM — pin hangs above the point
          eyeOffset: new Cartesian3(0, 0, -500), // slight depth offset so it renders above debris dots
        },
      })
      pinEntitiesRef.current[id] = entity
    },

    /** Remove a previously added pin entity by id. */
    removePinEntity(id) {
      const viewer = viewerRef.current?.cesiumElement
      if (!viewer) return
      if (pinEntitiesRef.current[id]) {
        viewer.entities.remove(pinEntitiesRef.current[id])
        delete pinEntitiesRef.current[id]
      }
    },
  }))


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

          // Dot sizing: visited route dots get a slight boost.
          let color
          let pixelSize = isVisited ? riskSize(debris.risk_score) + 2 : riskSize(debris.risk_score)

          const isPinned = pinnedIds?.has(debris.norad_id) ?? false
          const isActive = debris.norad_id === activeDebrisId

          const isDiffHighlighted = diffHighlightIds?.has(debris.norad_id) ?? false

          if (customSelecting && customSelectedIds) {
            // Custom selection mode.
            // Selected dots → cyan highlight + size boost.
            // Unselected dots → dim ONLY when a filter is actively engaged.
            //   No filter set → every dot stays full opacity.
            const isSelected = customSelectedIds.has(debris.norad_id)
            const filterActive =
              customFilterConfig &&
              (customFilterConfig.minRisk > 0 || customFilterConfig.methods.length > 0)

            let passesFilter = true
            if (filterActive) {
              // AND logic: object must satisfy both risk and method axes.
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
          } else {
            // Normal mode (not custom-selecting).
            if (isActive) {
              pixelSize = riskSize(debris.risk_score) + 4
            } else if (isPinned) {
              pixelSize = riskSize(debris.risk_score) + 2
            }
            // Diff-highlight overrides risk color with cyan when viewing a replan tab
            // and this stop wasn't present in the previous tab (added / changed stop).
            if (isDiffHighlighted) {
              color = Color.fromCssColorString('#00e5ff').withAlpha(0.95)
              pixelSize = riskSize(debris.risk_score) + 6
            } else if (focusMode === 'dim' && visitedIds !== null && !isVisited) {
              // HIGHLIGHT mode: dim all non-route dots to near-invisible so the
              // route stops read clearly against a quiet background.
              color = riskColor(debris.risk_score).withAlpha(0.18)
            } else {
              color = riskColor(debris.risk_score)
            }
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
            width={routeStyle === 'solid' ? 4 : 2}
            material={
              routeStyle === 'solid'
                ? new PolylineDashMaterialProperty({
                    color: Color.fromCssColorString('#ff6b35'),
                    gapColor: Color.fromCssColorString('#ff6b35').withAlpha(0.25),
                    dashLength: 24,
                    dashPattern: 0xff00,
                    dashOffset: dashOffset,
                  })
                : new PolylineDashMaterialProperty({ color: Color.fromCssColorString('#8A8A8E').withAlpha(0.85), dashLength: 16 })
            }
          />
        </Entity>
      )}
    </Viewer>
    </div>
  )
})

export default DebrisGlobe