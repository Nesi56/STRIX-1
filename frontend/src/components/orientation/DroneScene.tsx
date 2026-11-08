// Three.js scene (lazy-loaded). 1 scene unit = 100 mm.
// ZMR250: 250 mm motor-to-motor diagonal → each motor 125 mm from centre.
// Orientation mapping (aviation → three.js, forward = -Z, up = +Y):
//   pitch (nose up +) → rotation about +X; roll (right side down +) → about -Z;
//   relative yaw (nose right +) → about -Y; Euler order YXZ (yaw, pitch, roll).
import { Grid, OrbitControls } from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { forwardRef, useImperativeHandle, useMemo, useRef } from "react";
import * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import { telemetry } from "../../hooks/useTelemetry";

const D2R = Math.PI / 180;
const ARM = 1.25; // 125 mm from centre to each motor along the diagonal

function Arm({ angle, front }: { angle: number; front: boolean }) {
  const x = Math.sin(angle) * ARM;
  const z = -Math.cos(angle) * ARM;
  return (
    <group>
      <mesh position={[x / 2, 0, z / 2]} rotation={[0, -angle, 0]}>
        <boxGeometry args={[0.16, 0.06, ARM]} />
        <meshStandardMaterial color={front ? "#1e6e45" : "#2b322d"} metalness={0.3} roughness={0.6} />
      </mesh>
      <mesh position={[x, 0.09, z]}>
        <cylinderGeometry args={[0.13, 0.13, 0.16, 24]} />
        <meshStandardMaterial color="#9aa6b8" metalness={0.8} roughness={0.3} />
      </mesh>
      <mesh position={[x, 0.2, z]} rotation={[-Math.PI / 2, 0, 0]}>
        <circleGeometry args={[0.635, 40]} />
        <meshStandardMaterial color={front ? "#3ddc84" : "#ff4d5e"} transparent opacity={0.18} side={THREE.DoubleSide} />
      </mesh>
    </group>
  );
}

export function Quad({ smoothing, lockYaw, cameraTilt }: { smoothing: boolean; lockYaw: boolean; cameraTilt: number | null }) {
  const group = useRef<THREE.Group>(null);
  const target = useMemo(() => new THREE.Quaternion(), []);
  const euler = useMemo(() => new THREE.Euler(0, 0, 0, "YXZ"), []);
  useFrame((_, dt) => {
    const s = telemetry.latestImu;
    const g = group.current;
    if (!s || !g) return;
    const o = s.orientation;
    euler.set(o.pitch * D2R, lockYaw ? 0 : -o.yaw_relative * D2R, -o.roll * D2R, "YXZ");
    target.setFromEuler(euler);
    // Short time constant (40 ms) only bridges the 20 Hz update gaps.
    if (smoothing) g.quaternion.slerp(target, 1 - Math.exp(-dt / 0.04));
    else g.quaternion.copy(target);
  });
  const tilt = cameraTilt ?? 0;
  return (
    <group ref={group}>
      {[45, 135, 225, 315].map((a) => <Arm key={a} angle={a * D2R} front={a === 45 || a === 315} />)}
      <mesh>
        <boxGeometry args={[0.55, 0.12, 1.1]} />
        <meshStandardMaterial color="#141b26" metalness={0.2} roughness={0.8} />
      </mesh>
      <mesh position={[0, 0.16, 0.05]}>
        <boxGeometry args={[0.45, 0.2, 0.55]} />
        <meshStandardMaterial color="#0f2236" metalness={0.4} roughness={0.5} />
      </mesh>
      {/* camera on tilt servo */}
      <group position={[0, 0.12, -0.62]} rotation={[tilt * D2R, 0, 0]}>
        <mesh>
          <boxGeometry args={[0.26, 0.2, 0.12]} />
          <meshStandardMaterial color="#05080d" />
        </mesh>
        <mesh position={[0, 0, -0.08]} rotation={[Math.PI / 2, 0, 0]}>
          <cylinderGeometry args={[0.06, 0.06, 0.06, 20]} />
          <meshStandardMaterial color="#3ddc84" emissive="#0f4a2c" />
        </mesh>
      </group>
      {/* nose arrow */}
      <mesh position={[0, 0.02, -1.0]} rotation={[-Math.PI / 2, 0, 0]}>
        <coneGeometry args={[0.1, 0.3, 3]} />
        <meshStandardMaterial color="#3ddc84" emissive="#1e6e45" />
      </mesh>
      <axesHelper args={[0.9]} />
    </group>
  );
}

export interface DroneSceneHandle { resetView: () => void }

const DroneScene = forwardRef<DroneSceneHandle, { smoothing: boolean; lockYaw: boolean; cameraTilt: number | null }>(
  function DroneScene(props, ref) {
    const controls = useRef<OrbitControlsImpl | null>(null);
    useImperativeHandle(ref, () => ({ resetView: () => controls.current?.reset() }));
    return (
      <Canvas camera={{ position: [2.6, 1.9, 3.2], fov: 45 }} dpr={[1, 2]} gl={{ antialias: true }}>
        <color attach="background" args={["#0a0c0b"]} />
        <ambientLight intensity={0.55} />
        <directionalLight position={[3, 5, 2]} intensity={1.3} />
        <directionalLight position={[-3, 2, -4]} intensity={0.4} color="#9fe7bf" />
        <Quad {...props} />
        {/* Reference horizon plane and world axes (fixed, not geographic) */}
        <Grid position={[0, -0.6, 0]} args={[20, 20]} cellSize={0.5} cellThickness={0.5} cellColor="#1c2a21"
          sectionSize={2.5} sectionThickness={1} sectionColor="#1e6e45" fadeDistance={22} infiniteGrid />
        <axesHelper args={[1.6]} position={[0, -0.6, 0]} />
        <OrbitControls ref={controls} makeDefault enablePan={false} minDistance={2} maxDistance={12} />
      </Canvas>
    );
  },
);

export default DroneScene;
