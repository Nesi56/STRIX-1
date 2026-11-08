import { ChatPanel } from "../components/console/ChatPanel";
import { ManualControls } from "../components/console/ManualControls";
import { PromptsPanel } from "../components/console/PromptsPanel";
import { RightTabs } from "../components/console/RightTabs";
import { VideoFeed } from "../components/console/VideoFeed";

/** Single-screen operator console: controls | live YOLO video | agent + chat. */
export function ConsolePage() {
  return (
    <div className="grid gap-2 p-2 lg:h-full lg:min-h-0 lg:grid-cols-[270px_minmax(0,1fr)_390px] 2xl:grid-cols-[300px_minmax(0,1fr)_440px]">
      <div className="order-2 flex min-h-0 flex-col gap-2 lg:order-1">
        <div className="min-h-0 lg:flex-[1.35]"><ManualControls /></div>
        <div className="min-h-0 lg:flex-1"><PromptsPanel /></div>
      </div>
      <div className="order-1 h-[64vw] max-h-[70vh] min-h-[260px] lg:order-2 lg:h-auto lg:max-h-none lg:min-h-0"><VideoFeed /></div>
      <div className="order-3 flex min-h-0 flex-col gap-2">
        <div className="min-h-[300px] lg:min-h-0 lg:flex-[1.05]"><RightTabs /></div>
        <div className="h-[420px] lg:h-auto lg:min-h-0 lg:flex-1"><ChatPanel /></div>
      </div>
    </div>
  );
}
