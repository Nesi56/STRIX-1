import { ChatPanel } from "../components/console/ChatPanel";
import { DronePanel } from "../components/console/DronePanel";
import { MemoryPanel } from "../components/console/MemoryPanel";
import { ServoPanel } from "../components/console/ServoPanel";
import { StatusCorner } from "../components/console/StatusCorner";
import { VideoFeed } from "../components/console/VideoFeed";
import { useServoKeys } from "../hooks/useServoKeys";

/** One screen: status, camera servo and memory | camera | live 3D above the Qwen chat. */
export function ConsolePage() {
  useServoKeys();
  return (
    <div className="grid gap-2 p-2 lg:h-full lg:min-h-0 lg:grid-cols-[280px_minmax(0,1fr)_400px] 2xl:grid-cols-[300px_minmax(0,1fr)_440px]">
      <div className="order-2 flex min-h-0 flex-col gap-2 lg:order-1">
        <StatusCorner />
        <ServoPanel className="shrink-0" />
        <div className="h-[300px] lg:h-auto lg:min-h-0 lg:flex-1"><MemoryPanel /></div>
      </div>
      <div className="order-1 flex min-h-0 flex-col gap-2 lg:order-2">
        <div className="h-[64vw] max-h-[75vh] min-h-[260px] lg:h-full lg:max-h-none lg:min-h-0"><VideoFeed /></div>
      </div>
      <div className="order-3 flex min-h-0 flex-col gap-2">
        <div className="h-[300px] shrink-0 lg:h-auto lg:min-h-0 lg:flex-1"><DronePanel /></div>
        <div className="h-[520px] lg:h-auto lg:min-h-0 lg:flex-[1.35]"><ChatPanel /></div>
      </div>
    </div>
  );
}
