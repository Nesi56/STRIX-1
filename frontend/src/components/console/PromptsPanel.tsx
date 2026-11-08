import { Plus, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useTelemetry } from "../../hooks/useTelemetry";
import { errorMessage, get, post } from "../../services/api";
import { useToast } from "../common/toast";
import { ConsolePanel } from "./ConsolePanel";

export function PromptsPanel() {
  const st = useTelemetry();
  const det = st?.detector;
  const toast = useToast();
  const [classes, setClasses] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const prompts = det?.prompts ?? st?.detections?.prompts ?? [];

  useEffect(() => {
    get<{ classes: string[] }>("/api/detector/classes").then((r) => setClasses(r.classes)).catch(() => {});
  }, [det?.engine]);

  const save = async (next: string[]) => {
    try {
      await post("/api/detector/prompts", { prompts: next });
    } catch (e) {
      toast(errorMessage(e), "error");
    }
  };
  const add = async () => {
    const items = input.split(",").map((s) => s.trim()).filter(Boolean);
    if (!items.length) return;
    await save([...prompts, ...items]);
    setInput("");
  };
  const ok = det && ["ok", "synthetic"].includes(det.status.state);

  return (
    <ConsolePanel title="YOLO Prompts" status={det ? { ok: !!ok, label: ok ? "OK" : det.status.state } : null}>
      <div className="flex flex-wrap gap-1">
        {prompts.length === 0 && <span className="text-[11px] text-muted">No prompts — showing every class.</span>}
        {prompts.map((p) => (
          <span key={p} className="inline-flex items-center gap-1 rounded-[3px] border border-line-2 bg-[#1a201c] px-1.5 py-0.5 text-[11px]">
            {p}
            <button aria-label={`remove ${p}`} className="text-muted hover:text-crit" onClick={() => void save(prompts.filter((x) => x !== p))}><X size={11} /></button>
          </span>
        ))}
      </div>
      <form className="mt-2 flex gap-1" onSubmit={(e) => { e.preventDefault(); void add(); }}>
        <input className="input min-w-0 flex-1" list="strix-classes" placeholder="+ prompt (e.g. car, dog, chair)" value={input}
          onChange={(e) => setInput(e.target.value)} aria-label="Add detection prompt" />
        <datalist id="strix-classes">{classes.map((c) => <option key={c} value={c} />)}</datalist>
        <button className="btn btn-sm" disabled={!input.trim()} title="Add prompt"><Plus size={12} /></button>
      </form>
      <div className="mt-2 space-y-0.5 text-[10.5px] text-muted">
        <div>engine: <span className="text-text">{det?.description ?? "—"}</span></div>
        <div>rate: <span className="text-text">{det ? `${det.fps.toFixed(1)} fps` : "—"}</span>{det?.infer_ms != null && <> · inference <span className="text-text">{det.infer_ms.toFixed(0)} ms</span></>}</div>
        <div className="text-dim">Distances: {det?.distance_method ?? "pinhole size prior"}. Detector vocabulary: {det?.vocabulary_size ?? 80} classes.</div>
        {det?.engine === "hog" && <div className="text-warn">No YOLO model installed — person-only HOG fallback. Run scripts/get_detector_model.sh.</div>}
      </div>
    </ConsolePanel>
  );
}
