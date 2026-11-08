import { useState } from "react";
import { AgentGraph } from "./AgentGraph";
import { RadarView } from "./RadarView";
import { RecordingsTab } from "./RecordingsTab";

type Tab = "graph" | "radar" | "recordings";
const TABS: { id: Tab; label: string }[] = [
  { id: "graph", label: "Agent Graph" },
  { id: "radar", label: "Radar" },
  { id: "recordings", label: "Recordings" },
];

export function RightTabs() {
  const [tab, setTab] = useState<Tab>("graph");
  return (
    <section className="panel flex h-full min-h-0 flex-col">
      <header className="panel-head flex gap-1 overflow-x-auto px-2 py-1.5">
        {TABS.map((t) => <button key={t.id} className={`chip-tab shrink-0 ${tab === t.id ? "active" : ""}`} onClick={() => setTab(t.id)}>{t.label}</button>)}
      </header>
      <div className="min-h-0 flex-1 overflow-auto p-2.5">
        {tab === "graph" && <AgentGraph />}
        {tab === "radar" && <RadarView />}
        {tab === "recordings" && <RecordingsTab />}
      </div>
    </section>
  );
}
