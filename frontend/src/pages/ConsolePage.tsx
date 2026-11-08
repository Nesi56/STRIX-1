import { ChatPanel } from "../components/console/ChatPanel";
import { DronePanel } from "../components/console/DronePanel";
import { ManualControls } from "../components/console/ManualControls";
import { PromptsPanel } from "../components/console/PromptsPanel";
import { RightTabs } from "../components/console/RightTabs";
import { StatusCorner } from "../components/console/StatusCorner";
import { VideoFeed } from "../components/console/VideoFeed";

/** Everything on one screen: status + controls | camera + 3D drone | agent/radar/recordings + Qwen chat. */
export function ConsolePage() {
  return (
    <div className="grid gap-2 p-2 lg:h-full lg:min-h-0 lg:grid-cols-[270px_minmax(0,1fr)_400px] 2xl:grid-cols-[290px_minmax(0,1fr)_440px]">
      <div className="order-2 flex min-h-0 flex-col gap-2 lg:order-1">
        <StatusCorner />
        <div className="min-h-0 lg:flex-[1.45]"><ManualControls /></div>
        <div className="min-h-0 lg:flex-1"><PromptsPanel /></div>
      </div>
      <div className="order-1 flex min-h-0 flex-col gap-2 lg:order-2">
        <div className="h-[64vw] max-h-[70vh] min-h-[260px] lg:h-auto lg:max-h-none lg:min-h-0 lg:flex-[1.8]"><VideoFeed /></div>
        <div className="h-[300px] lg:h-auto lg:min-h-0 lg:flex-1"><DronePanel /></div>
      </div>
      <div className="order-3 flex min-h-0 flex-col gap-2">
        <div className="min-h-[280px] lg:min-h-0 lg:flex-1"><RightTabs /></div>
        <div className="h-[460px] lg:h-auto lg:min-h-0 lg:flex-[1.3]"><ChatPanel /></div>
      </div>
    </div>
  );
}
