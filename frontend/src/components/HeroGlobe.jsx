import { useEffect, useRef } from 'react';

// Purely decorative. Altitude is visually exaggerated here (debris pushed
// further out) so the field reads as a dramatic "ring" around the limb —
// the real mission console (GlobeView.jsx) keeps true altitude scale.
// This component owns its own Cesium Viewer instance; it never shares
// state with the console globe.

const ALTITUDE_EXAGGERATION = 14; // artistic only — real console does NOT do this
const RING_COLOR_HIGH = { r: 0xff, g: 0xd8, b: 0xa0 }; // warm bright — mimics reference's glow
const RING_COLOR_LOW = { r: 0x6a, g: 0x7a, b: 0x92 };  // cool dim

function ringColor(Cesium, t) {
  const r = Math.round(RING_COLOR_LOW.r + (RING_COLOR_HIGH.r - RING_COLOR_LOW.r) * t);
  const g = Math.round(RING_COLOR_LOW.g + (RING_COLOR_HIGH.g - RING_COLOR_LOW.g) * t);
  const b = Math.round(RING_COLOR_LOW.b + (RING_COLOR_HIGH.b - RING_COLOR_LOW.b) * t);
  return Cesium.Color.fromBytes(r, g, b);
}

export default function HeroGlobe({ debrisField }) {
  const containerRef = useRef(null);
  const viewerRef = useRef(null);
  const rafRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    let headingAngle = 0;

    async function init() {
      const Cesium = window.Cesium;
      if (!Cesium || !containerRef.current) return;

      const imageryProvider = await Cesium.TileMapServiceImageryProvider.fromUrl(
        Cesium.buildModuleUrl('Assets/Textures/NaturalEarthII'),
        { credit: 'Imagery: NASA Natural Earth II (bundled with CesiumJS)' }
      );

      if (cancelled) return;

      // NOTE: same fix as GlobeView.jsx — `imageryProvider` is a defunct
      // Viewer constructor option in this pinned CesiumJS version (1.120).
      // Passing it there gets silently ignored and Viewer falls back to
      // its default ion-backed base layer, which never resolves without an
      // ion token — leaving a bare, untextured globe. `baseLayer: false`
      // skips that default, and we attach our own imagery layer manually
      // right after construction.
      const viewer = new Cesium.Viewer(containerRef.current, {
        baseLayer: false,
        terrainProvider: new Cesium.EllipsoidTerrainProvider(),
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
        creditContainer: document.createElement('div'), // hide credit text on the hero
      });

      viewer.imageryLayers.addImageryProvider(imageryProvider);

      viewer.scene.screenSpaceCameraController.enableInputs = false; // decorative only, no user drag
      viewer.resolutionScale = Math.min(window.devicePixelRatio || 1, 2);
      viewer.scene.postProcessStages.fxaa.enabled = true;
      viewer.scene.globe.enableLighting = true;
      viewer.scene.globe.maximumScreenSpaceError = 3;
      viewer.scene.skyAtmosphere.hueShift = -0.02;

      // Bloom gives the bright limb/debris-glow that soft, slightly
      // overexposed cinematic look instead of flat CG shading.
      const bloom = viewer.scene.postProcessStages.bloom;
      bloom.enabled = true;
      bloom.uniforms.glowOnly = false;
      bloom.uniforms.contrast = 140;
      bloom.uniforms.brightness = -0.25;
      bloom.uniforms.delta = 1.1;
      bloom.uniforms.sigma = 3.0;
      bloom.uniforms.stepSize = 3.0;

      // Debris ring, exaggerated altitude for visual drama.
      debrisField.forEach((d) => {
        const exaggeratedAlt = d.altitude_km * 1000 * ALTITUDE_EXAGGERATION;
        const position = Cesium.Cartesian3.fromDegrees(d.longitude, d.latitude, exaggeratedAlt);
        viewer.entities.add({
          position,
          point: {
            pixelSize: 2 + d.risk_score * 3,
            color: ringColor(Cesium, d.risk_score).withAlpha(0.85),
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
          },
        });
      });

      viewerRef.current = viewer;

      // Continuous slow camera orbit around Earth's center. (Previous version
      // manually combined fromDegrees + heading/pitch, which pointed the
      // camera along the local horizon at that point instead of at the
      // planet — mostly empty space, Earth barely/never in frame. lookAt()
      // orbits around a fixed target and guarantees it stays framed.)
      const range = 22_000_000;
      const pitch = Cesium.Math.toRadians(-30);

      function tick() {
        if (cancelled) return;
        headingAngle += 0.0006;
        viewer.camera.lookAt(
          Cesium.Cartesian3.ZERO,
          new Cesium.HeadingPitchRange(headingAngle, pitch, range)
        );
        viewer.scene.requestRender();
        rafRef.current = requestAnimationFrame(tick);
      }
      tick();
    }

    init();
    return () => {
      cancelled = true;
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      viewerRef.current?.destroy();
      viewerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <div ref={containerRef} className="hero-globe-canvas" />;
}