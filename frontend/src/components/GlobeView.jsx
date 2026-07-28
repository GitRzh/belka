import { useEffect, useRef } from 'react';

const RISK_HIGH = { r: 0xba, g: 0x75, b: 0x17 };   // amber
const RISK_LOW = { r: 0x88, g: 0x87, b: 0x80 };    // muted slate
const ROUTE_COLOR = '#1D9E75';                      // teal — optimized route
const NAIVE_COLOR = '#8a94a6';                       // grey — naive baseline
const LAUNCH_COLOR = '#4dd8e6';

function riskColor(Cesium, risk) {
  const t = Math.max(0, Math.min(1, risk));
  const r = Math.round(RISK_LOW.r + (RISK_HIGH.r - RISK_LOW.r) * t);
  const g = Math.round(RISK_LOW.g + (RISK_HIGH.g - RISK_LOW.g) * t);
  const b = Math.round(RISK_LOW.b + (RISK_HIGH.b - RISK_LOW.b) * t);
  return Cesium.Color.fromBytes(r, g, b);
}

export default function GlobeView({ debrisField, plan, naiveRoute, showNaive, selectedNoradId, onSelectDebris, launchSite, autoRotate = false }) {
  const containerRef = useRef(null);
  const viewerRef = useRef(null);
  const pointsRef = useRef(new Map());
  const rotateIntervalRef = useRef(null);

  // Init viewer once.
  useEffect(() => {
    let cancelled = false;

    async function init() {
      const Cesium = window.Cesium;
      if (!Cesium) {
        console.error('Cesium failed to load from CDN — check index.html script tag / network.');
        return;
      }

      const ionToken = import.meta.env.VITE_CESIUM_ION_TOKEN;
      let imageryProvider, terrainProvider;

      if (ionToken) {
        Cesium.Ion.defaultAccessToken = ionToken;
        imageryProvider = await Cesium.createWorldImageryAsync();
        terrainProvider = await Cesium.createWorldTerrainAsync();
      } else {
        // Fully free/offline path: no ion account, no quota risk, and — this
        // matters more than either of those — no external tile server at
        // all. Both the earlier OpenStreetMap attempt (no CORS headers) and
        // the ArcGIS attempt (blocked by this container's network) failed.
        // This uses the low-res Natural Earth II imagery bundled INSIDE the
        // CesiumJS download itself, fetched from the same cesium.com domain
        // that already successfully served Cesium.js — nothing new to reach.
        imageryProvider = await Cesium.TileMapServiceImageryProvider.fromUrl(
          Cesium.buildModuleUrl('Assets/Textures/NaturalEarthII'),
          { credit: 'Imagery: NASA Natural Earth II (bundled with CesiumJS)' }
        );
        terrainProvider = new Cesium.EllipsoidTerrainProvider();
      }

      if (cancelled) return;

      // NOTE: `imageryProvider` is NOT passed into the Viewer constructor.
      // As of modern CesiumJS (this project pins 1.120), the constructor's
      // `imageryProvider` option is defunct — passing it is silently
      // ignored, and Cesium falls back to trying its default ion-backed
      // base layer instead. With no ion token configured (see .env.example
      // — that's intentional here) that default layer never resolves, so
      // the globe rendered with no imagery at all: a bare untextured
      // ellipsoid, which is why it looked like a flat blue circle instead
      // of an actual textured globe. `baseLayer: false` stops Viewer from
      // trying to set up that default layer, and we add our own imagery
      // provider explicitly right after construction instead.
      const viewer = new Cesium.Viewer(containerRef.current, {
        baseLayer: false,
        terrainProvider,
        baseLayerPicker: false,
        timeline: false,
        animation: false,
        fullscreenButton: false,
        homeButton: false,
        geocoder: false,
        navigationHelpButton: false,
        sceneModePicker: false,
        infoBox: false,
        selectionIndicator: false,
      });

      viewer.imageryLayers.addImageryProvider(imageryProvider);

      // Performance: only redraw on change, not every frame.
      viewer.scene.requestRenderMode = true;
      viewer.scene.maximumRenderTimeChange = Infinity;
      viewer.resolutionScale = Math.min(window.devicePixelRatio || 1, 2);
      viewer.scene.postProcessStages.fxaa.enabled = true;
      viewer.scene.globe.enableLighting = true;
      viewer.scene.globe.maximumScreenSpaceError = 3;

      viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(20, 10, 9_000_000),
        duration: 0,
      });

      const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
      handler.setInputAction((movement) => {
        const picked = viewer.scene.pick(movement.position);
        if (picked?.id?.noradId) {
          onSelectDebris(picked.id.noradId);
          viewer.scene.requestRender();
        }
      }, Cesium.ScreenSpaceEventType.LEFT_CLICK);

      viewerRef.current = viewer;
      renderScene();

      if (autoRotate) {
        rotateIntervalRef.current = setInterval(() => {
          viewer.scene.camera.rotate(Cesium.Cartesian3.UNIT_Z, -0.0015);
          viewer.scene.requestRender();
        }, 33);
      }
    }

    init();
    return () => {
      cancelled = true;
      if (rotateIntervalRef.current) clearInterval(rotateIntervalRef.current);
      viewerRef.current?.destroy();
      viewerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function renderScene() {
    const Cesium = window.Cesium;
    const viewer = viewerRef.current;
    if (!Cesium || !viewer) return;

    viewer.entities.removeAll();
    pointsRef.current.clear();

    const activePlan = showNaive ? naiveRoute : plan;
    const routeIds = new Set((activePlan?.route ?? []).map(String));

    // Launch site marker.
    if (launchSite) {
      viewer.entities.add({
        position: Cesium.Cartesian3.fromDegrees(launchSite.lon, launchSite.lat, 0),
        point: { pixelSize: 10, color: Cesium.Color.fromCssColorString(LAUNCH_COLOR), outlineColor: Cesium.Color.BLACK, outlineWidth: 1 },
        label: { text: 'LAUNCH', font: '11px "JetBrains Mono"', pixelOffset: new Cesium.Cartesian2(0, -18), fillColor: Cesium.Color.fromCssColorString(LAUNCH_COLOR) },
      });
    }

    // Debris points.
    debrisField.forEach((d) => {
      const isSelected = String(d.norad_id) === String(selectedNoradId);
      const isInRoute = routeIds.has(String(d.norad_id));
      const position = Cesium.Cartesian3.fromDegrees(d.longitude, d.latitude, d.altitude_km * 1000);
      const color = d.removal_method === 'monitor_only' ? Cesium.Color.GRAY.withAlpha(0.5) : riskColor(Cesium, d.risk_score);

      const entity = viewer.entities.add({
        position,
        noradId: d.norad_id,
        point: {
          pixelSize: isSelected ? 9 : isInRoute ? 5 : 2.5,
          color: isInRoute || isSelected ? color : color.withAlpha(0.75),
          outlineColor: isSelected ? Cesium.Color.WHITE : Cesium.Color.TRANSPARENT,
          outlineWidth: isSelected ? 1.5 : 0,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
        label: isSelected
          ? { text: d.name, font: '11px "JetBrains Mono"', pixelOffset: new Cesium.Cartesian2(0, -16), fillColor: Cesium.Color.WHITE }
          : undefined,
      });
      pointsRef.current.set(d.norad_id, entity);
    });

    // Route polyline, in visit order.
    if (activePlan?.route?.length) {
      const positions = activePlan.route
        .map((id) => debrisField.find((d) => d.norad_id === id))
        .filter(Boolean)
        .map((d) => Cesium.Cartesian3.fromDegrees(d.longitude, d.latitude, d.altitude_km * 1000));

      if (launchSite) {
        positions.unshift(Cesium.Cartesian3.fromDegrees(launchSite.lon, launchSite.lat, 0));
      }

      viewer.entities.add({
        polyline: {
          positions,
          width: 2,
          material: new Cesium.PolylineDashMaterialProperty({
            color: Cesium.Color.fromCssColorString(showNaive ? NAIVE_COLOR : ROUTE_COLOR),
          }),
        },
      });
    }

    viewer.scene.requestRender();
  }

  useEffect(() => {
    renderScene();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debrisField, plan, naiveRoute, showNaive, selectedNoradId, launchSite]);

  return <div ref={containerRef} className="globe-container" />;
}