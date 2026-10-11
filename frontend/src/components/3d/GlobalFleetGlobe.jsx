import React, { Suspense, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { Bounds, Html, OrbitControls, useBounds, useCursor } from '@react-three/drei';
import { Globe } from 'lucide-react';
import { Card } from '../ui';
import { FLEET_CAMERA_FOV, STATUS_STYLE, phaseFor, pulseAt } from '../../lib/fleetStatus';
import {
  GLOBE_RADIUS,
  HIT_RADIUS,
  PIN_RADIUS,
  REGION_SLOTS,
  facingDot,
  gaugeWidth,
  graticulePositions,
  latLonToVector3,
  localVisual,
  matchNodesToSlots,
  overviewDistance,
  slotVisual,
  surfaceNormal,
} from '../../lib/globe';
import usePrefersReducedMotion from './usePrefersReducedMotion';

const R = GLOBE_RADIUS;
const UP = new THREE.Vector3(0, 1, 0);
const ORIGIN = new THREE.Vector3(0, 0, 0);

const PILLAR_RADIUS = 0.022;
const PIN_LIFT = 0.03;
const PIN_ACTIVE_SCALE = 1.5;
const RING_SIZE = [0.045, 0.062, 32];
const LABEL_LIFT = 0.12;
const GROW_RATE = 4; // per second, for exponential easing toward new pillar height/colour
const EMISSIVE_BASE = 0.35;
// Hide labels just before the limb so they never float over the globe's silhouette.
const FACING_MIN = 0.1;
// R3F fires onClick after a drag that started on a pin; ignore anything that moved more than this.
const CLICK_SLOP = 4;

const SPIN_SPEED = 0.06; // rad/s
const ATMOSPHERE_SCALE = 1.12;
const GRATICULE_STEP = 30;
const GRATICULE_SEGMENTS = 96;

// Fixed boxes keep every fly-to at a known distance regardless of pillar height.
const HOME_BOX = new THREE.Box3().setFromCenterAndSize(ORIGIN, new THREE.Vector3().setScalar(2 * (R + 0.25)));
const FOCUS_BOX_SIZE = new THREE.Vector3().setScalar(R);
const FOCUS_LIFT = 0.15;
const BOUNDS_MARGIN = 1.4;
const FLY_SECONDS = 0.9;
const REDUCED_FLY_SECONDS = 0.01;
const MIN_DISTANCE = 1.8;
const MAX_DISTANCE = 8;
const HOME_DIR = new THREE.Vector3(...surfaceNormal(30, 120)).add(new THREE.Vector3(0, 0.25, 0)).normalize();
const HOME_DISTANCE = 4.7;
// The local slot neighbours Seoul, so its label is nudged sideways to avoid stacking on Seoul's.
const LABEL_SHIFT = 'translateY(-50%)';
const LOCAL_LABEL_SHIFT = 'translate(-60%, -50%)';
// Keep drei's tooltip below app overlays (modals use z-50).
const TOOLTIP_Z = [30, 0];

function Earth({ onSurfaceClick }) {
  const graticule = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(graticulePositions(R * 1.002, GRATICULE_STEP, GRATICULE_STEP, GRATICULE_SEGMENTS), 3));
    return g;
  }, []);
  useEffect(() => () => graticule.dispose(), [graticule]);

  return (
    <>
      {/* Opaque to pointer events so pins on the far side cannot be hovered or clicked through the globe. */}
      <mesh
        onPointerOver={(e) => e.stopPropagation()}
        onPointerMove={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation();
          if (e.delta <= CLICK_SLOP) onSurfaceClick();
        }}
      >
        <sphereGeometry args={[R, 64, 48]} />
        <meshStandardMaterial color="#0f1a33" emissive="#0b1226" emissiveIntensity={0.5} roughness={0.85} metalness={0.1} />
      </mesh>
      <lineSegments geometry={graticule}>
        <lineBasicMaterial color="#3b5b8f" transparent opacity={0.35} depthWrite={false} />
      </lineSegments>
      <mesh scale={ATMOSPHERE_SCALE}>
        <sphereGeometry args={[R, 48, 32]} />
        <meshBasicMaterial color="#6366f1" side={THREE.BackSide} transparent opacity={0.14} blending={THREE.AdditiveBlending} depthWrite={false} />
      </mesh>
    </>
  );
}

function CpuGauge({ visual }) {
  return (
    <div className="mt-1.5">
      <div className="flex items-center justify-between gap-3 text-[10px] text-slate-400">
        <span title="1분 load average / 코어 수. CPU 사용률과 다른 부하 지표입니다.">CPU 부하</span>
        <span className="font-mono tabular-nums text-slate-200">{visual.cpuPct}%</span>
      </div>
      <div className="mt-0.5 text-[9px] text-slate-500">1분 load / core</div>
      <div className="mt-0.5 h-1.5 w-32 overflow-hidden rounded-full bg-white/10">
        <div className="h-full rounded-full" style={{ width: `${gaugeWidth(visual.cpuPct)}%`, backgroundColor: visual.color }} />
      </div>
    </div>
  );
}

function metricNote(slot, node, visual) {
  if (slot.local) return `백엔드 ${visual.label}`;
  if (!node) return visual.label;
  if (!node.ok) return '접속 불가';
  if (visual.cpuRatio == null && visual.memRatio == null) return '부하 측정값 없음';
  return null;
}

function SlotPanel({ slot, node, visual }) {
  const title = node?.node ?? slot.label;
  const region = node ? node.region || node.zone : slot.region;
  const where = [slot.label, node?.provider, region, node?.host].filter(Boolean).join(' · ');
  const note = metricNote(slot, node, visual);
  return (
    <div className="whitespace-nowrap rounded-lg border border-white/10 bg-slate-950/90 px-2.5 py-1.5 text-[11px] text-slate-300 shadow-xl shadow-black/40 backdrop-blur">
      <div className="font-mono text-[12px] font-semibold text-slate-100">{title}</div>
      <div className="mt-0.5 font-mono text-[10px] text-slate-500">{where}</div>
      <div className="mt-1 inline-flex items-center gap-1">
        <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: visual.color }} />
        {slot.local ? '로컬 핑' : visual.label}
      </div>
      {visual.cpuPct != null && <CpuGauge visual={visual} />}
      {visual.memPct != null && (
        <div className="mt-1 flex items-center justify-between text-[10px] text-slate-400">
          <span>메모리</span>
          <span className="font-mono tabular-nums text-slate-200">{visual.memPct}%</span>
        </div>
      )}
      {note && <div className="mt-1 text-[10px] text-slate-500">{note}</div>}
    </div>
  );
}

function SlotChip({ slot, visual }) {
  return (
    <div className="inline-flex items-center gap-1 whitespace-nowrap rounded-md bg-slate-950/75 px-1.5 py-0.5 text-[10px] text-slate-300 ring-1 ring-inset ring-white/10">
      <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: visual.color }} />
      {slot.label}
    </div>
  );
}

function Slot({ slot, node, visual, index, active, reduced, geometries, register, onHover, onSelect, pauseRef, portal }) {
  const groupRef = useRef(null);
  const pillarRef = useRef(null);
  const hitRef = useRef(null);
  const pinRef = useRef(null);
  const labelRef = useRef(null);
  const panelRef = useRef(null);
  const pillarMat = useRef(null);
  const pinMat = useRef(null);
  const ringMat = useRef(null);
  const height = useRef(reduced ? visual.height : 0);
  const color = useRef(new THREE.Color(visual.color));
  const shown = useRef(true);
  const scratch = useMemo(() => ({ world: new THREE.Vector3(), p: [0, 0, 0], c: [0, 0, 0] }), []);

  const position = useMemo(() => latLonToVector3(slot.lat, slot.lon, R), [slot.lat, slot.lon]);
  const quaternion = useMemo(
    () => new THREE.Quaternion().setFromUnitVectors(UP, new THREE.Vector3(...surfaceNormal(slot.lat, slot.lon))),
    [slot.lat, slot.lon],
  );
  const target = useMemo(() => new THREE.Color(visual.color), [visual.color]);

  useEffect(() => {
    register(slot.id, groupRef.current);
    return () => register(slot.id, null);
  }, [register, slot.id]);

  useFrame((state, delta) => {
    const k = reduced ? 1 : 1 - Math.exp(-GROW_RATE * delta);
    height.current += (visual.height - height.current) * k;
    color.current.lerp(target, k);
    const h = height.current;

    pillarRef.current.scale.y = h;
    pillarRef.current.position.y = h / 2;
    hitRef.current.scale.y = h + PIN_LIFT * 2;
    hitRef.current.position.y = (h + PIN_LIFT * 2) / 2;
    pinRef.current.position.y = h + PIN_LIFT;
    pinRef.current.scale.setScalar(active ? PIN_ACTIVE_SCALE : 1);
    labelRef.current.position.y = h + LABEL_LIFT;

    const style = STATUS_STYLE[visual.status];
    const mix = style && !reduced ? pulseAt(visual.status, state.clock.elapsedTime, phaseFor(index), 1).mix : 0;
    for (const mat of [pillarMat.current, pinMat.current]) {
      mat.color.copy(color.current);
      mat.emissive.copy(color.current);
      mat.emissiveIntensity = Math.max(0, EMISSIVE_BASE + mix);
    }
    ringMat.current.color.copy(color.current);

    // Far-side check: hide the label when its surface point faces away from the camera.
    groupRef.current.getWorldPosition(scratch.world).toArray(scratch.p);
    state.camera.position.toArray(scratch.c);
    const visible = facingDot(scratch.p, scratch.c) > FACING_MIN;
    if (visible !== shown.current && panelRef.current) {
      shown.current = visible;
      panelRef.current.style.display = visible ? '' : 'none';
    }
  });

  return (
    <group
      ref={groupRef}
      position={position}
      quaternion={quaternion}
      onPointerOver={(e) => {
        e.stopPropagation();
        onHover(slot.id);
      }}
      onPointerOut={(e) => {
        e.stopPropagation();
        onHover((h) => (h === slot.id ? null : h));
      }}
      onClick={(e) => {
        e.stopPropagation();
        if (e.delta > CLICK_SLOP) return;
        pauseRef.current = true; // stop the spin before the fly-to reads this group's world position
        onSelect(slot.id);
      }}
    >
      <mesh rotation-x={-Math.PI / 2} position-y={0.002} geometry={geometries.ring}>
        <meshBasicMaterial ref={ringMat} transparent opacity={0.75} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
      <mesh ref={pillarRef} geometry={geometries.pillar}>
        <meshStandardMaterial ref={pillarMat} roughness={0.35} metalness={0.2} toneMapped={false} />
      </mesh>
      <mesh ref={pinRef} geometry={geometries.pin}>
        <meshStandardMaterial ref={pinMat} roughness={0.25} metalness={0.1} toneMapped={false} />
      </mesh>
      {/* Invisible fat cylinder so the thin pillar is easy to hit. */}
      <mesh ref={hitRef} geometry={geometries.hit}>
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>
      <group ref={labelRef}>
        <Html center zIndexRange={TOOLTIP_Z} pointerEvents="none" wrapperClass="pointer-events-none" style={{ pointerEvents: 'none' }} portal={portal}>
          <div ref={panelRef} className="pointer-events-none" style={{ transform: slot.local ? LOCAL_LABEL_SHIFT : LABEL_SHIFT }}>
            {active ? <SlotPanel slot={slot} node={node} visual={visual} /> : <SlotChip slot={slot} visual={visual} />}
          </div>
        </Html>
      </group>
    </group>
  );
}

// Drives drei Bounds from focus state. A focused slot flies the camera out along its surface normal while the
// orbit target stays at the globe centre, so dragging afterwards still spins around the whole globe.
function FocusController({ focusedId, slotGroups }) {
  const bounds = useBounds();
  const { size, camera } = useThree();
  useEffect(() => {
    if (!focusedId) {
      bounds.refresh(HOME_BOX).moveTo(HOME_DIR.clone().multiplyScalar(overviewDistance(camera.aspect, camera.fov))).lookAt({ target: ORIGIN });
      return;
    }
    const group = slotGroups.current.get(focusedId);
    if (!group) return;
    group.updateWorldMatrix(true, false);
    const surface = group.getWorldPosition(new THREE.Vector3());
    const normal = surface.clone().normalize();
    const center = surface.addScaledVector(normal, FOCUS_LIFT);
    const { distance } = bounds.refresh(new THREE.Box3().setFromCenterAndSize(center, FOCUS_BOX_SIZE)).getSize();
    bounds.moveTo(center.clone().addScaledVector(normal, distance)).lookAt({ target: ORIGIN });
  }, [bounds, focusedId, slotGroups, camera, size.width, size.height]);
  return null;
}

function GlobeScene({ entries, focusedId, onFocus, reduced, interactiveZoom, portal }) {
  const aspect = useThree((state) => state.size.width / state.size.height);
  const [hovered, setHovered] = useState(null);
  const spinRef = useRef(null);
  const pauseRef = useRef(false);
  const slotGroups = useRef(new Map());
  useCursor(hovered != null);

  const geometries = useMemo(
    () => ({
      pillar: new THREE.CylinderGeometry(PILLAR_RADIUS, PILLAR_RADIUS, 1, 16),
      hit: new THREE.CylinderGeometry(HIT_RADIUS, HIT_RADIUS, 1, 8),
      pin: new THREE.SphereGeometry(PIN_RADIUS, 20, 14),
      ring: new THREE.RingGeometry(...RING_SIZE),
    }),
    [],
  );
  useEffect(() => () => Object.values(geometries).forEach((g) => g.dispose()), [geometries]);

  const register = useMemo(
    () => (id, group) => {
      if (group) slotGroups.current.set(id, group);
      else slotGroups.current.delete(id);
    },
    [],
  );

  useLayoutEffect(() => {
    pauseRef.current = reduced || focusedId != null || hovered != null;
  }, [reduced, focusedId, hovered]);

  useFrame((_, delta) => {
    if (!pauseRef.current && spinRef.current) spinRef.current.rotation.y += SPIN_SPEED * delta;
  });

  return (
    <>
      <ambientLight intensity={0.35} />
      <hemisphereLight args={['#c7d2fe', '#0a0d14', 0.6]} />
      <directionalLight position={[4, 3, 5]} intensity={1.3} />
      <directionalLight position={[-5, -2, -4]} intensity={0.45} color="#8b5cf6" />
      <Bounds margin={BOUNDS_MARGIN} maxDuration={reduced ? REDUCED_FLY_SECONDS : FLY_SECONDS}>
        <group ref={spinRef}>
          <Earth onSurfaceClick={() => onFocus(null)} />
          {entries.map(({ slot, node, visual }, i) => (
            <Slot
              key={slot.id}
              slot={slot}
              node={node}
              visual={visual}
              index={i}
              active={focusedId === slot.id || hovered === slot.id}
              reduced={reduced}
              geometries={geometries}
              register={register}
              onHover={setHovered}
              onSelect={(id) => onFocus((cur) => (cur === id ? null : id))}
              pauseRef={pauseRef}
              portal={portal}
            />
          ))}
        </group>
        <FocusController focusedId={focusedId} slotGroups={slotGroups} />
      </Bounds>
      <OrbitControls
        makeDefault
        enableDamping
        dampingFactor={0.08}
        enablePan={false}
        enableZoom={interactiveZoom} // the card sits in a scrolling page; only the stage owns the wheel
        minDistance={MIN_DISTANCE}
        maxDistance={Math.max(MAX_DISTANCE, overviewDistance(aspect, FLEET_CAMERA_FOV) * 1.25)}
      />
    </>
  );
}

function slotStatusText({ slot, visual }) {
  if (slot.local) return `백엔드 ${visual.label}`;
  if (visual.cpuPct != null) return `${visual.label} · CPU 부하 ${visual.cpuPct}%`;
  return visual.label;
}

function SlotLegend({ entries, focusedId, onFocus, unplaced }) {
  return (
    <div className="pointer-events-none absolute bottom-3 left-3 right-3 flex flex-wrap items-center gap-1.5">
      <ul aria-label="리전별 노드 상태" className="contents">
        {entries.map((entry) => {
          const { slot, visual } = entry;
          const pressed = focusedId === slot.id;
          return (
            <li key={slot.id}>
              <button
                type="button"
                aria-pressed={pressed}
                onClick={() => onFocus(pressed ? null : slot.id)}
                className={`pointer-events-auto inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] ring-1 ring-inset backdrop-blur transition-colors focus:outline-none focus-visible:ring-violet-400 ${pressed ? 'bg-violet-500/20 text-slate-100 ring-violet-400/40' : 'bg-slate-950/70 text-slate-300 ring-white/10 hover:bg-slate-900/80'}`}
              >
                <span className="h-2 w-2 rounded-full" style={{ backgroundColor: visual.color }} />
                <span className="font-medium">{slot.label}</span>
                <span className="text-slate-400">{slotStatusText(entry)}</span>
              </button>
            </li>
          );
        })}
      </ul>
      {unplaced > 0 && (
        <span className="pointer-events-none rounded-md bg-slate-950/70 px-2 py-1 text-[11px] text-slate-400 ring-1 ring-inset ring-white/10">
          +{unplaced} 미배치 노드
        </span>
      )}
    </div>
  );
}

export default function GlobalFleetGlobe({ fleet, healthy = null, presentation = 'stage', className = '', height = 320 }) {
  const reduced = usePrefersReducedMotion();
  const [focusedId, setFocusedId] = useState(null);
  // fixed label target: otherwise drei re-mounts every Html root once R3F connects its events
  const labelLayer = useRef(null);
  const { entries, unplaced } = useMemo(() => {
    const matched = matchNodesToSlots(fleet);
    return {
      unplaced: matched.unplaced,
      entries: matched.slots.map(({ slot, node }) => ({ slot, node, visual: slot.local ? localVisual(healthy) : slotVisual(node) })),
    };
  }, [fleet, healthy]);
  const stage = presentation === 'stage';
  const placed = entries.filter((e) => e.node).length;

  const view = (
    <div ref={labelLayer} className={`relative overflow-hidden ${stage ? 'scene-embedded' : 'rounded-xl border border-white/5 bg-[#0a0d14]'}`} style={{ height: stage ? '100%' : height }}>
      <Canvas
        dpr={[1, 1.75]}
        camera={{ fov: FLEET_CAMERA_FOV, near: 0.05, far: 100, position: HOME_DIR.clone().multiplyScalar(HOME_DISTANCE).toArray() }}
        gl={{ antialias: true, alpha: true }}
        aria-label={`글로벌 노드 풀 3D 지구본, 리전 ${REGION_SLOTS.length - 1}곳 중 ${placed}곳 연결`}
        onPointerMissed={() => setFocusedId(null)}
      >
        <Suspense fallback={null}>
          <GlobeScene entries={entries} focusedId={focusedId} onFocus={setFocusedId} reduced={reduced} interactiveZoom={stage} portal={labelLayer} />
        </Suspense>
      </Canvas>
      <SlotLegend entries={entries} focusedId={focusedId} onFocus={setFocusedId} unplaced={unplaced} />
      <div className="pointer-events-none absolute right-3 top-3 flex items-center gap-1.5">
        {focusedId && (
          <button
            type="button"
            onClick={() => setFocusedId(null)}
            className="pointer-events-auto rounded-md bg-slate-950/80 px-2 py-1 text-[11px] text-slate-200 ring-1 ring-inset ring-white/15 hover:bg-slate-900 focus:outline-none focus-visible:ring-violet-400"
          >
            전체 보기
          </button>
        )}
        <span className="rounded-md bg-slate-950/70 px-2 py-1 font-mono text-[10px] text-violet-300/90 ring-1 ring-inset ring-violet-500/20">
          드래그로 회전 · 핀 클릭으로 확대
        </span>
      </div>
    </div>
  );
  if (stage) return view;
  return (
    <Card title="글로벌 노드 맵" icon={Globe} className={className} right={<span className="text-[11px] text-slate-500">리전 {placed}/{REGION_SLOTS.length - 1}곳 연결</span>}>
      {view}
    </Card>
  );
}
