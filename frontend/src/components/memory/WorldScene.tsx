// Live 3D view of the object memory (lazy-loaded). 1 unit = 1 m, drone-centred:
// three.js x right, y up, −z forward at yaw 0 (memory frame z forward → −z).
// The drone model moves with the MPU6050; the cone is where the camera looks (IMU yaw/pitch + servo tilt).
// SEEN objects are solid, REMEMBERED ones are ghosts, HIDDEN ones glow inside their see-through container.
// Labels are canvas-texture sprites (no DOM overlays inside the canvas).
import { Grid, OrbitControls } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import { telemetry } from "../../hooks/useTelemetry";
import { dir, MEM_COLOR, ROUND, sizeM, stateShort, toScene } from "../../lib/memory";
import type { MemoryObject, MemoryState } from "../../types/telemetry";
import { Quad } from "../orientation/DroneScene";

type V3 = [number, number, number];

// ------------------------------------------------------------------ labels
function Label({ lines, color, position, lift = 0 }: { lines: [string, string?]; color: string; position: V3; lift?: number }) {
  const key = `${lines[0]}|${lines[1] ?? ""}|${color}`;
  const H = lines[1] ? 160 : 92;
  // GPU texture storage is immutable in size, so a label that changes height gets a new canvas + texture.
  const canvas = useMemo(() => {
    const c = document.createElement("canvas");
    c.width = 1024;
    c.height = H;
    return c;
  }, [H]);
  const tex = useMemo(() => {
    const t = new THREE.CanvasTexture(canvas);
    t.colorSpace = THREE.SRGBColorSpace;
    t.anisotropy = 4;
    return t;
  }, [canvas]);
  useEffect(() => () => tex.dispose(), [tex]);
  useEffect(() => {
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.font = "700 54px ui-monospace, Menlo, monospace";
    const w1 = ctx.measureText(lines[0]).width;
    ctx.font = "500 42px ui-monospace, Menlo, monospace";
    const w2 = lines[1] ? ctx.measureText(lines[1]).width : 0;
    const scale = Math.min(1, (canvas.width - 40) / Math.max(w1, w2, 1));
    const bw = Math.max(w1, w2) * scale + 32;
    const bh = lines[1] ? 136 : 84;
    ctx.save();
    ctx.translate(canvas.width / 2, canvas.height - bh / 2 - 4);
    ctx.fillStyle = "rgba(4,8,6,0.82)";
    ctx.strokeStyle = color;
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.roundRect(-bw / 2, -bh / 2, bw, bh, 12);
    ctx.fill();
    ctx.stroke();
    ctx.scale(scale, scale);
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = color;
    ctx.font = "700 54px ui-monospace, Menlo, monospace";
    ctx.fillText(lines[0], 0, lines[1] ? -28 / scale : 0);
    if (lines[1]) {
      ctx.fillStyle = "#e8eef0";
      ctx.font = "500 42px ui-monospace, Menlo, monospace";
      ctx.fillText(lines[1], 0, 30 / scale);
    }
    ctx.restore();
    tex.needsUpdate = true;
  }, [key, canvas, tex]);
  const h = 0.06 * (H / 160);
  return (
    <sprite position={position} scale={[h * (canvas.width / H), h, 1]} center={new THREE.Vector2(0.5, -lift)} renderOrder={10}>
      <spriteMaterial map={tex} transparent depthTest={false} depthWrite={false} sizeAttenuation={false} />
    </sprite>
  );
}

// ------------------------------------------------------------------ objects
interface Placed { o: MemoryObject; pos: V3; w: number; h: number; dist: number }

function place(mem: MemoryState): Placed[] {
  const byId = new Map(mem.objects.map((o) => [o.id, o]));
  const base = new Map<number, Placed>();
  for (const o of mem.objects) {
    const { pos, k } = toScene(o.xyz);
    const s = sizeM(o);
    base.set(o.id, { o, pos, w: s.w * k, h: s.h * k, dist: Math.hypot(...pos) });
  }
  // a hidden object sits on the table inside its container
  for (const p of base.values()) {
    const c = p.o.memory === "hidden" && p.o.contained_by != null ? base.get(p.o.contained_by) : undefined;
    if (c && byId.has(c.o.id)) {
      const r = Math.min(p.w, p.h, c.w * 0.7, c.h * 0.8) / 2;
      p.w = p.h = 2 * r;
      p.pos = [c.pos[0], c.pos[1] - c.h / 2 + r, c.pos[2]];
    }
  }
  return [...base.values()];
}

/** The container a hidden object is inside (if it is placed in this view). */
function holder(p: Placed, ids: Map<number, Placed>): Placed | undefined {
  return p.o.memory === "hidden" && p.o.contained_by != null ? ids.get(p.o.contained_by) : undefined;
}

/** Labels sit on top of the object; a hidden object's label is stacked above its container's label. */
function anchor(p: Placed, ids: Map<number, Placed>): V3 {
  const q = holder(p, ids) ?? p;
  return [q.pos[0], q.pos[1] + q.h / 2 + 0.005, q.pos[2]];
}

function Container({ p, contains }: { p: Placed; contains: boolean }) {
  const r2 = (v: number) => Math.round(v * 200) / 200;
  const w = r2(p.w);
  const h = r2(p.h);
  const geom = useMemo(() => new THREE.CylinderGeometry(w * 0.36, w * 0.5, h, 36, 1), [w, h]);
  const edges = useMemo(() => new THREE.EdgesGeometry(geom, 30), [geom]);
  useEffect(() => () => { geom.dispose(); edges.dispose(); }, [geom, edges]);
  const color = MEM_COLOR[p.o.memory];
  return (
    <group position={p.pos}>
      <mesh geometry={geom} renderOrder={2}>
        <meshStandardMaterial color="#d23c46" transparent opacity={p.o.memory === "seen" ? (contains ? 0.28 : 0.55) : 0.16}
          depthWrite={false} side={THREE.DoubleSide} roughness={0.4} />
      </mesh>
      <lineSegments geometry={edges} renderOrder={3}>
        <lineBasicMaterial color={contains ? MEM_COLOR.hidden : color} transparent opacity={p.o.memory === "seen" ? 1 : 0.6} />
      </lineSegments>
    </group>
  );
}

function Thing({ p }: { p: Placed }) {
  const mat = useRef<THREE.MeshStandardMaterial>(null);
  const kind = p.o.memory;
  const color = MEM_COLOR[kind];
  useFrame(({ clock }) => {
    if (mat.current && kind === "hidden") mat.current.emissiveIntensity = 0.75 + 0.5 * Math.sin(clock.elapsedTime * 5);
  });
  const round = ROUND.has(p.o.label);
  const body = p.o.label === "sports ball" ? "#ff8c1a" : p.o.label === "person" ? "#5a7bd8" : "#8fa3b8";
  return (
    <group position={p.pos}>
      <mesh renderOrder={1}>
        {round ? <sphereGeometry args={[Math.min(p.w, p.h) / 2, 28, 18]} /> : <boxGeometry args={[p.w, p.h, Math.max(0.02, p.w * 0.6)]} />}
        <meshStandardMaterial ref={mat} color={body} emissive={kind === "hidden" ? MEM_COLOR.hidden : "#000000"}
          emissiveIntensity={kind === "hidden" ? 1 : 0} transparent={kind === "remembered"} opacity={kind === "remembered" ? 0.3 : 1}
          wireframe={kind === "remembered"} />
      </mesh>
      {kind !== "hidden" && (
        <mesh>
          {round ? <sphereGeometry args={[Math.min(p.w, p.h) / 2 + 0.004, 16, 10]} /> : <boxGeometry args={[p.w + 0.01, p.h + 0.01, Math.max(0.03, p.w * 0.6 + 0.01)]} />}
          <meshBasicMaterial color={color} wireframe transparent opacity={kind === "seen" ? 0.55 : 0.35} />
        </mesh>
      )}
    </group>
  );
}

function SightLine({ to, color }: { to: V3; color: string }) {
  const line = useMemo(() => {
    const g = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(...to)]);
    const l = new THREE.Line(g, new THREE.LineDashedMaterial({ color, dashSize: 0.03, gapSize: 0.025, transparent: true, opacity: 0.7 }));
    l.computeLineDistances();
    return l;
  }, [to, color]);
  useEffect(() => () => { line.geometry.dispose(); (line.material as THREE.Material).dispose(); }, [line]);
  return <primitive object={line} />;
}

// ------------------------------------------------------------------ camera view cone (live from IMU + servo)
function ViewCone({ halfH, halfV, length }: { halfH: number; halfV: number; length: number }) {
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(16 * 3), 3));
    return g;
  }, []);
  const face = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(6 * 3), 3));
    return g;
  }, []);
  useEffect(() => () => { geom.dispose(); face.dispose(); }, [geom, face]);
  useFrame(() => {
    const o = telemetry.latestImu?.orientation;
    const tilt = telemetry.snap.state?.servo?.tilt_deg ?? 0;
    const az = o?.yaw_relative ?? 0;
    const el = (o?.pitch ?? 0) + tilt;
    const c = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([sx, sy]) => dir(az + sx * halfH, el + sy * halfV).map((v) => v * length));
    const pts = [
      [0, 0, 0], c[0], [0, 0, 0], c[1], [0, 0, 0], c[2], [0, 0, 0], c[3],
      c[0], c[1], c[1], c[2], c[2], c[3], c[3], c[0],
    ].flat();
    (geom.attributes.position as THREE.BufferAttribute).set(pts);
    geom.attributes.position.needsUpdate = true;
    (face.attributes.position as THREE.BufferAttribute).set([c[0], c[1], c[2], c[0], c[2], c[3]].flat());
    face.attributes.position.needsUpdate = true;
    geom.computeBoundingSphere();
    face.computeBoundingSphere();
  });
  return (
    <group>
      <lineSegments geometry={geom}>
        <lineBasicMaterial color="#39ff7a" transparent opacity={0.45} />
      </lineSegments>
      <mesh geometry={face}>
        <meshBasicMaterial color="#39ff7a" transparent opacity={0.06} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
    </group>
  );
}

// ------------------------------------------------------------------ fitting the view
function Fitter({ placed, fitKey }: { placed: Placed[]; fitKey: string }) {
  const camera = useThree((s) => s.camera);
  const controls = useThree((s) => s.controls) as OrbitControlsImpl | null;
  const ref = useRef(placed);
  ref.current = placed;
  useEffect(() => {
    if (!controls) return;
    const objs = ref.current.map((p) => new THREE.Vector3(...p.pos));
    const box = new THREE.Box3().setFromPoints([new THREE.Vector3(0, 0, 0), ...objs]);
    const objCenter = objs.length ? new THREE.Box3().setFromPoints(objs).getCenter(new THREE.Vector3()) : new THREE.Vector3();
    // look mostly at the objects, keep the drone in frame; view from the right side, slightly behind and above
    const center = objCenter.multiplyScalar(0.7);
    const radius = Math.max(0.4, box.getSize(new THREE.Vector3()).length() / 2);
    const offset = new THREE.Vector3(0.75, 0.8, 0.95).normalize().multiplyScalar(radius * 1.7);
    camera.position.copy(center.clone().add(offset));
    controls.target.copy(center);
    controls.update();
  }, [fitKey, controls, camera]);
  return null;
}

interface Props { mem: MemoryState | null; serverNow: number; fitNonce: number }

export default function WorldScene({ mem, serverNow, fitNonce }: Props) {
  const placed = useMemo(() => (mem ? place(mem) : []), [mem]);
  const ids = new Map(placed.map((p) => [p.o.id, p]));
  const near = placed.filter((p) => p.dist < 4);
  const floorY = near.length ? Math.min(...near.map((p) => p.pos[1] - p.h / 2)) - 0.003 : -0.5;
  const coneLen = Math.max(0.7, Math.min(2.4, Math.max(0, ...placed.map((p) => p.dist)) * 1.08));
  const sceneName = mem?.scene?.scene ?? "real";
  const fitKey = `${fitNonce}|${sceneName}|${placed.length > 0}`;
  const tilt = telemetry.snap.state?.servo?.tilt_deg ?? null;
  return (
    <Canvas camera={{ position: [0.6, 0.8, 1.2], fov: 45, near: 0.01, far: 100 }} dpr={[1, 2]} gl={{ antialias: true }}>
      <color attach="background" args={["#0a0c0b"]} />
      <ambientLight intensity={0.6} />
      <directionalLight position={[2, 4, 2]} intensity={1.3} />
      <directionalLight position={[-2, 1.5, -3]} intensity={0.35} color="#9fe7bf" />
      <group scale={0.1}>
        <Quad smoothing lockYaw={false} cameraTilt={tilt} />
      </group>
      <ViewCone halfH={mem?.camera.half_h ?? 51} halfV={mem?.camera.half_v ?? 35} length={coneLen} />
      {placed.map((p) => {
        const kind = p.o.memory;
        let lines: [string, string?] = [p.o.name, stateShort(p.o, mem as MemoryState, serverNow)];
        if (p.o.container && p.o.contains.length) {
          lines[1] = `${stateShort(p.o, mem as MemoryState, serverNow)} · holds ${p.o.contains.map((id) => ids.get(id)?.o.name ?? `#${id}`).join(", ")}`;
        } else if (kind === "seen") {
          lines = [`${p.o.name} · SEEN`];
        }
        return (
          <group key={p.o.id}>
            {p.o.container ? <Container p={p} contains={p.o.contains.length > 0} /> : <Thing p={p} />}
            {kind !== "seen" && <SightLine to={p.pos} color={MEM_COLOR[kind]} />}
            <Label lines={lines} color={kind === "hidden" ? MEM_COLOR.hidden : MEM_COLOR[kind]}
              position={anchor(p, ids)} lift={kind === "hidden" && holder(p, ids) ? 1.3 : 0.15} />
          </group>
        );
      })}
      <Grid position={[0, floorY, 0]} args={[10, 10]} cellSize={0.1} cellThickness={0.5} cellColor="#1c2a21"
        sectionSize={0.5} sectionThickness={1} sectionColor="#1f5670" fadeDistance={9} infiniteGrid />
      <OrbitControls makeDefault enablePan minDistance={0.25} maxDistance={30} />
      <Fitter placed={placed} fitKey={fitKey} />
    </Canvas>
  );
}
