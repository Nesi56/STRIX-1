import { useEffect, useState } from "react";

/** STRIX-1 mark: an owl's eye inside a chevron (strix = owl). */
export function BrandMark({ size = 24, color = "var(--color-accent)" }: { size?: number; color?: string }) {
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} aria-hidden>
      <path d="M2 9 L16 27 L30 9 L22 9 L16 18 L10 9 Z" fill="none" stroke={color} strokeWidth="1.8" strokeLinejoin="miter" />
      <circle cx="16" cy="7" r="3.4" fill="none" stroke={color} strokeWidth="1.6" />
      <circle cx="16" cy="7" r="1.2" fill={color} />
    </svg>
  );
}

export function Wordmark({ sub }: { sub?: string }) {
  return (
    <div className="flex items-center gap-2.5">
      <BrandMark />
      <div className="leading-none">
        <div className="text-[18px] font-bold tracking-[0.22em] text-text">STRIX-1</div>
        {sub && <div className="mt-1 text-[10px] font-semibold tracking-[0.2em] text-dim">{sub}</div>}
      </div>
    </div>
  );
}

/** UTC clock, once a second. */
export function useUtcClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return now.toISOString().slice(11, 19) + "Z";
}
