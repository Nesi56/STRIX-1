import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export interface Series { key: string; name: string; color: string }

const tick = { fill: "#7d91ab", fontSize: 10, fontFamily: "ui-monospace, monospace" };

export function LiveChart(props: {
  data: ReadonlyArray<object>;
  xKey: string;
  series: Series[];
  unit: string;
  height?: number;
  domain?: [number | "auto" | "dataMin" | "dataMax", number | "auto" | "dataMin" | "dataMax"];
  xFormatter?: (v: number) => string;
  referenceX?: number;
}) {
  return (
    <div style={{ height: props.height ?? 160 }} className="w-full min-w-0">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={props.data as object[]} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="#13223a" strokeDasharray="2 4" />
          <XAxis dataKey={props.xKey} type="number" domain={["dataMin", "dataMax"]} tick={tick} tickFormatter={props.xFormatter}
            stroke="#22385a" minTickGap={40} allowDataOverflow />
          <YAxis tick={tick} stroke="#22385a" domain={props.domain ?? ["auto", "auto"]} width={52}
            tickFormatter={(v: number) => `${Number(v).toFixed(Math.abs(v) < 10 ? 1 : 0)}`} />
          <Tooltip
            isAnimationActive={false}
            contentStyle={{ background: "#08101d", border: "1px solid #22385a", fontSize: 11, fontFamily: "ui-monospace, monospace" }}
            labelFormatter={(v) => (props.xFormatter ? props.xFormatter(Number(v)) : String(v))}
            formatter={(v) => [`${Number(v).toFixed(3)} ${props.unit}`]}
          />
          <Legend wrapperStyle={{ fontSize: 10, fontFamily: "ui-monospace, monospace" }} iconSize={8} height={16} />
          {props.referenceX !== undefined && <ReferenceLine x={props.referenceX} stroke="#38d6ff" strokeDasharray="3 3" />}
          {props.series.map((s) => (
            <Line key={s.key} type="linear" dataKey={s.key} name={s.name} stroke={s.color} dot={false} strokeWidth={1.4}
              isAnimationActive={false} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export const AXIS_COLORS = { x: "#ff5468", y: "#34d58a", z: "#4c8dff" };
