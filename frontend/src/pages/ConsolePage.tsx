import { ChatPanel } from "../components/console/ChatPanel";
import { DronePanel } from "../components/console/DronePanel";
import { MemoryPanel } from "../components/console/MemoryPanel";
import { StatusCorner } from "../components/console/StatusCorner";
import { VideoFeed } from "../components/console/VideoFeed";
import { useServoKeys } from "../hooks/useServoKeys";

/** Everything on one screen: status + memory | camera + live 3D | Qwen & YOLO (and recordings). */
export function ConsolePage() {
  useServoKeys();
  return (
    <div className="grid gap-2 p-2 lg:h-full lg:min-h-0 lg:grid-cols-[260px_minmax(0,1fr)_400px] 2xl:grid-cols-[280px_minmax(0,1fr)_440px]">
      <div className="order-2 flex min-h-0 flex-col gap-2 lg:order-1">
        <StatusCorner />
        <div className="h-[320px] lg:h-auto lg:min-h-0 lg:flex-1"><MemoryPanel /></div>
      </div>
      <div className="order-1 flex min-h-0 flex-col gap-2 lg:order-2">
        <div className="h-[64vw] max-h-[70vh] min-h-[260px] lg:h-auto lg:max-h-none lg:min-h-0 lg:flex-[1.8]"><VideoFeed /></div>
        <div className="h-[300px] lg:h-auto lg:min-h-0 lg:flex-1"><DronePanel /></div>
      </div>
      <div className="order-3 h-[560px] min-h-0 lg:h-auto"><ChatPanel /></div>
    </div>
  );
}
