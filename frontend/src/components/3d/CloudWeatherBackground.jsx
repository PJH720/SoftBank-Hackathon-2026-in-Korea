import { Suspense, memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { Cloud, Clouds, Sparkles } from '@react-three/drei';
import * as THREE from 'three';
import { WEATHER_PRESETS, flashLevel, parallaxTarget, presetFor } from '../../lib/weather';
import SceneBoundary from './SceneBoundary';
import usePrefersReducedMotion from './usePrefersReducedMotion';

const CAMERA_Z = 8;
const CAMERA = { fov: 40, near: 0.1, far: 100, position: [0, 0, CAMERA_Z] };
const DPR = [1, 1.5];
const GL = { alpha: true, antialias: false, powerPreference: 'low-power' };
// The canvas is decorative: R3F must not attach pointer listeners or raycast.
const disabledEvents = () => ({ enabled: false, priority: 0 });

const ROOT_STYLE = { position: 'absolute', inset: 0, pointerEvents: 'none', overflow: 'hidden' };
// R3F's wrapper div sets pointer-events inline, so the canvas needs its own override.
const CANVAS_STYLE = { position: 'absolute', inset: 0, pointerEvents: 'none' };
const TINT_STYLE = { position: 'absolute', inset: 0, transition: 'background-color 1s ease' };

// Exponential damping rates (1/s); ~3 settles a cross-fade in about a second.
const FADE_LAMBDA = 3;
const PARALLAX_LAMBDA = 2.5;
const GONE_OPACITY = 0.02;

const AMBIENT_INTENSITY = 1.1;
const KEY_LIGHT_INTENSITY = 0.8;
const KEY_LIGHT_POS = [3, 4, 5];
const ACCENT_LIGHT_POS = [0, 1.5, 3];
const FLASH_LOW = 0.15;
const REDUCED_FLASH_LEVEL = 0.45;

const CLOUD_Z = -1;
const CLOUD_SEGMENTS = 6;
const CLOUD_FADE = 2;
// x is a fraction of the half viewport width, so puffs spread across a wide, short header.
const CLOUD_SLOTS = [
  { x: -0.8, y: 0.3, seed: 11, bounds: [2.4, 0.4, 0.8], volume: 2 },
  { x: -0.4, y: -0.2, seed: 23, bounds: [2.8, 0.5, 0.8], volume: 2.4 },
  { x: 0, y: 0, seed: 37, bounds: [3.2, 0.5, 1], volume: 2.8 },
  { x: 0.4, y: 0.25, seed: 41, bounds: [2.8, 0.4, 0.8], volume: 2.4 },
  { x: 0.8, y: -0.1, seed: 53, bounds: [2.4, 0.4, 0.8], volume: 2 },
];
const CLOUD_LIMIT = CLOUD_SLOTS.length * CLOUD_SEGMENTS;

const DRIFT_WIDTH_RATIO = 0.95;
const RING_CLUSTERS = 8;
const RING_TILT = 0.32;
const RING_RADIUS_RATIO = 0.12;
const RING_RADIUS_MIN = 2.5;
const RING_RADIUS_MAX = 6;
const SWIRL_INNER_RATIO = 0.6;
const SWIRL_OUTER_SHARE = 0.6;
const SWIRL_COUNTER_SPEED = 1.4;
const HEAL_BOB_HZ = 0.25;
const HEAL_BOB_HEIGHT = 0.25;
const HEAL_RISE = 0.2;

const TEXTURE_SIZE = 128;
let cloudTextureUrl;

// Soft radial puff drawn once and reused; the URL string doubles as the texture loader cache key.
function getCloudTexture() {
  if (cloudTextureUrl !== undefined) return cloudTextureUrl;
  const canvas = document.createElement('canvas');
  canvas.width = TEXTURE_SIZE;
  canvas.height = TEXTURE_SIZE;
  const ctx = canvas.getContext('2d');
  if (!ctx) {
    cloudTextureUrl = null;
    return cloudTextureUrl;
  }
  const half = TEXTURE_SIZE / 2;
  const gradient = ctx.createRadialGradient(half, half, 0, half, half, half);
  gradient.addColorStop(0, 'rgba(255,255,255,1)');
  gradient.addColorStop(0.45, 'rgba(255,255,255,0.65)');
  gradient.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, TEXTURE_SIZE, TEXTURE_SIZE);
  cloudTextureUrl = canvas.toDataURL('image/png');
  return cloudTextureUrl;
}

const fadeFactor = (delta) => 1 - Math.exp(-FADE_LAMBDA * delta);

function WeatherClouds({ preset, reduced }) {
  const cloudsRef = useRef(null);
  const meshRef = useRef(null);
  const groupRef = useRef(null);
  const targetColor = useMemo(() => new THREE.Color(), []);
  const viewportWidth = useThree((s) => s.viewport.width);
  const texture = useMemo(() => getCloudTexture(), []);
  const speed = reduced ? 0 : preset.cloud.speed;

  useFrame((_, delta) => {
    if (!meshRef.current) meshRef.current = cloudsRef.current?.children.find((c) => c.isInstancedMesh) ?? null;
    const material = meshRef.current?.material;
    const k = fadeFactor(delta);
    if (material) {
      material.color.lerp(targetColor.set(preset.cloud.color), k);
      material.opacity = THREE.MathUtils.lerp(material.opacity, preset.cloud.opacity, k);
    }
    const group = groupRef.current;
    if (group) group.scale.setScalar(THREE.MathUtils.lerp(group.scale.x, preset.cloud.volume, k));
  });

  if (!texture) return null;
  const halfWidth = viewportWidth / 2;
  return (
    <group ref={groupRef} position={[0, 0, CLOUD_Z]}>
      <Clouds ref={cloudsRef} texture={texture} limit={CLOUD_LIMIT} frustumCulled={false}>
        {CLOUD_SLOTS.map((slot) => (
          <Cloud
            key={slot.seed}
            seed={slot.seed}
            position={[slot.x * halfWidth, slot.y, 0]}
            bounds={slot.bounds}
            volume={slot.volume}
            growth={1.5}
            segments={CLOUD_SEGMENTS}
            fade={CLOUD_FADE}
            speed={speed}
            color="#ffffff"
            opacity={1}
          />
        ))}
      </Clouds>
    </group>
  );
}

function SparkleRing({ sparkles, count, radius, spinRef, reduced }) {
  const perCluster = Math.max(1, Math.round(count / RING_CLUSTERS));
  return (
    <group ref={spinRef}>
      {Array.from({ length: RING_CLUSTERS }, (_, i) => {
        const angle = (i / RING_CLUSTERS) * Math.PI * 2;
        return (
          <Sparkles
            key={i}
            position={[Math.cos(angle) * radius, 0, Math.sin(angle) * radius]}
            count={perCluster}
            scale={sparkles.scale}
            size={sparkles.size}
            speed={reduced ? 0 : sparkles.speed}
            opacity={0}
            color={sparkles.color}
          />
        );
      })}
    </group>
  );
}

// One mood's particles. Memoised and keyed per status so drei does not rebuild colour buffers each render.
const SparkleLayer = memo(function SparkleLayer({ id, sparkles, leaving, reduced, viewportWidth, onGone }) {
  const fadeRef = useRef(null);
  const outerRef = useRef(null);
  const innerRef = useRef(null);
  const goneRef = useRef(false);
  const opacityRef = useRef(0);
  const radius = THREE.MathUtils.clamp(viewportWidth * RING_RADIUS_RATIO, RING_RADIUS_MIN, RING_RADIUS_MAX);

  useFrame((state, delta) => {
    const fade = fadeRef.current;
    if (!fade) return;
    const target = leaving ? 0 : 1;
    const nextOpacity = THREE.MathUtils.lerp(opacityRef.current, target, fadeFactor(delta));
    const opacity = Math.abs(nextOpacity - target) < GONE_OPACITY ? target : nextOpacity;
    const changed = opacity !== opacityRef.current;
    opacityRef.current = opacity;
    // Read current geometry: responsive resizing can replace Drei's points buffers.
    fade.traverse((object) => {
      if (!object.isPoints) return;
      const attribute = object.geometry.attributes.opacity;
      if (!changed && attribute.array[0] === opacity) return;
      attribute.array.fill(opacity);
      attribute.needsUpdate = true;
    });
    if (leaving && opacity < GONE_OPACITY && !goneRef.current) {
      goneRef.current = true;
      onGone(id);
    }
    if (reduced) return;
    const spin = sparkles.spin * delta;
    if (outerRef.current) outerRef.current.rotation.y += spin;
    if (innerRef.current) innerRef.current.rotation.y -= spin * SWIRL_COUNTER_SPEED;
    if (sparkles.pattern === 'ring') {
      fade.position.y = HEAL_RISE + Math.sin(state.clock.elapsedTime * Math.PI * 2 * HEAL_BOB_HZ) * HEAL_BOB_HEIGHT;
    }
  });

  let content;
  if (sparkles.pattern === 'swirl') {
    const outer = Math.round(sparkles.count * SWIRL_OUTER_SHARE);
    content = (
      <group rotation={[RING_TILT, 0, 0]}>
        <SparkleRing sparkles={sparkles} count={outer} radius={radius} spinRef={outerRef} reduced={reduced} />
        <SparkleRing sparkles={sparkles} count={sparkles.count - outer} radius={radius * SWIRL_INNER_RATIO} spinRef={innerRef} reduced={reduced} />
      </group>
    );
  } else if (sparkles.pattern === 'ring') {
    content = (
      <group rotation={[RING_TILT, 0, 0]}>
        <SparkleRing sparkles={sparkles} count={sparkles.count} radius={radius} spinRef={outerRef} reduced={reduced} />
      </group>
    );
  } else {
    const [sx, sy, sz] = sparkles.scale;
    content = (
      <Sparkles
        count={sparkles.count}
        scale={[Math.max(sx, viewportWidth * DRIFT_WIDTH_RATIO), sy, sz]}
        size={sparkles.size}
        speed={reduced ? 0 : sparkles.speed}
        opacity={0}
        color={sparkles.color}
      />
    );
  }

  return (
    <group ref={fadeRef}>
      {content}
    </group>
  );
});

// Only the active and previous moods stay mounted, even during rapid status changes.
function SparkleLayers({ status, reduced }) {
  const viewportWidth = useThree((s) => s.viewport.width);
  const [layers, setLayers] = useState(() => [{ id: 0, status, leaving: false }]);
  const [shownStatus, setShownStatus] = useState(status);

  if (status !== shownStatus) {
    setShownStatus(status);
    setLayers((prev) => {
      const nextId = prev.reduce((max, l) => Math.max(max, l.id), 0) + 1;
      const outgoing = prev.filter((l) => !l.leaving).map((l) => ({ ...l, leaving: true }));
      return [...outgoing, { id: nextId, status, leaving: false }];
    });
  }

  const dropLayer = useCallback((id) => setLayers((prev) => prev.filter((l) => l.id !== id)), []);

  return layers.map((layer) => (
    <SparkleLayer
      key={layer.id}
      id={layer.id}
      sparkles={presetFor(layer.status).sparkles}
      leaving={layer.leaving}
      reduced={reduced}
      viewportWidth={viewportWidth}
      onGone={dropLayer}
    />
  ));
}

function WeatherLights({ preset, reduced }) {
  const accentRef = useRef(null);
  const baseIntensity = useRef(preset.light.intensity);
  const targetColor = useMemo(() => new THREE.Color(), []);

  useFrame((state, delta) => {
    const light = accentRef.current;
    if (!light) return;
    const k = fadeFactor(delta);
    light.color.lerp(targetColor.set(preset.light.color), k);
    baseIntensity.current = THREE.MathUtils.lerp(baseIntensity.current, preset.light.intensity, k);
    let factor = 1;
    // Reduced-motion users get a steady dim red instead of a strobe.
    if (preset.light.flash) factor = reduced ? REDUCED_FLASH_LEVEL : FLASH_LOW + (1 - FLASH_LOW) * flashLevel(state.clock.elapsedTime);
    light.intensity = baseIntensity.current * factor;
  });

  return (
    <>
      <ambientLight intensity={AMBIENT_INTENSITY} />
      <directionalLight position={KEY_LIGHT_POS} intensity={KEY_LIGHT_INTENSITY} />
      <pointLight ref={accentRef} position={ACCENT_LIGHT_POS} decay={0} color={preset.light.color} intensity={0} />
    </>
  );
}

// The canvas never receives pointer events, so parallax reads the window pointer instead.
function useWindowPointer(enabled) {
  const pointer = useRef([0, 0]);
  useEffect(() => {
    pointer.current = [0, 0];
    if (!enabled) return undefined;
    const onMove = (e) => {
      pointer.current = [(e.clientX / window.innerWidth) * 2 - 1, -((e.clientY / window.innerHeight) * 2 - 1)];
    };
    window.addEventListener('pointermove', onMove, { passive: true });
    return () => window.removeEventListener('pointermove', onMove);
  }, [enabled]);
  return pointer;
}

function ParallaxCamera({ reduced }) {
  const pointer = useWindowPointer(!reduced);
  useFrame(({ camera }, delta) => {
    const [tx, ty] = reduced ? [0, 0] : parallaxTarget(pointer.current[0], pointer.current[1]);
    camera.position.x = THREE.MathUtils.damp(camera.position.x, tx, PARALLAX_LAMBDA, delta);
    camera.position.y = THREE.MathUtils.damp(camera.position.y, ty, PARALLAX_LAMBDA, delta);
    camera.lookAt(0, 0, 0);
  });
  return null;
}

function WeatherScene({ status, reduced }) {
  const preset = presetFor(status);
  return (
    <>
      <ParallaxCamera reduced={reduced} />
      <WeatherLights preset={preset} reduced={reduced} />
      <Suspense fallback={null}>
        <WeatherClouds preset={preset} reduced={reduced} />
      </Suspense>
      <SparkleLayers status={status} reduced={reduced} />
    </>
  );
}

/** Decorative header backdrop whose clouds, particles and light follow the deployment status. */
export default function CloudWeatherBackground({ status = 'idle', className = '' }) {
  const reduced = usePrefersReducedMotion();
  const resolved = Object.hasOwn(WEATHER_PRESETS, status) ? status : 'idle';
  const preset = WEATHER_PRESETS[resolved];
  return (
    <div className={`weather-background ${className}`.trim()} style={ROOT_STYLE} aria-hidden="true">
      <div style={{ ...TINT_STYLE, backgroundColor: preset.background }} />
      <SceneBoundary fallback={null}>
        <Canvas style={CANVAS_STYLE} events={disabledEvents} dpr={DPR} camera={CAMERA} gl={GL}>
          <WeatherScene status={resolved} reduced={reduced} />
        </Canvas>
      </SceneBoundary>
    </div>
  );
}
