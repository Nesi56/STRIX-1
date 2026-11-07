// Classic attitude director indicator (artificial horizon) in SVG.
export function AttitudeIndicator({ roll, pitch, size = 150 }: { roll: number; pitch: number; size?: number }) {
  const r = size / 2;
  const ppd = r / 35; // pixels per degree of pitch
  const p = Math.max(-60, Math.min(60, pitch));
  const ladder = [-30, -20, -10, 10, 20, 30];
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`Attitude: roll ${roll.toFixed(1)}°, pitch ${pitch.toFixed(1)}°`}>
      <defs>
        <clipPath id="adi-clip"><circle cx={r} cy={r} r={r - 3} /></clipPath>
      </defs>
      <g clipPath="url(#adi-clip)">
        <g transform={`rotate(${-roll} ${r} ${r}) translate(0 ${p * ppd})`}>
          <rect x={-size} y={-size * 2 + r} width={size * 3} height={size * 2} fill="#0f3d66" />
          <rect x={-size} y={r} width={size * 3} height={size * 2} fill="#3a2a17" />
          <line x1={-size} x2={size * 2} y1={r} y2={r} stroke="#d4e2f3" strokeWidth={1.5} />
          {ladder.map((d) => (
            <g key={d}>
              <line x1={r - (d % 20 === 0 ? 22 : 12)} x2={r + (d % 20 === 0 ? 22 : 12)} y1={r - d * ppd} y2={r - d * ppd} stroke="#d4e2f3" strokeWidth={1} opacity={0.8} />
              {d % 20 === 0 && <text x={r + 26} y={r - d * ppd + 3} fontSize={8} fill="#d4e2f3" className="num">{Math.abs(d)}</text>}
            </g>
          ))}
        </g>
      </g>
      {/* roll scale */}
      {[-60, -45, -30, -20, -10, 0, 10, 20, 30, 45, 60].map((a) => (
        <line key={a} x1={r} x2={r} y1={4} y2={a % 30 === 0 ? 13 : 9} stroke="#7d91ab" strokeWidth={1} transform={`rotate(${a} ${r} ${r})`} />
      ))}
      <path d={`M ${r} 14 l -5 8 h 10 z`} fill="#38d6ff" transform={`rotate(${-roll} ${r} ${r})`} />
      {/* fixed aircraft symbol */}
      <path d={`M ${r - 34} ${r} h 22 l 6 7 l 6 -7 h 22`} fill="none" stroke="#38d6ff" strokeWidth={3} strokeLinejoin="round" />
      <circle cx={r} cy={r} r={2.5} fill="#38d6ff" />
      <circle cx={r} cy={r} r={r - 2} fill="none" stroke="#22385a" strokeWidth={3} />
    </svg>
  );
}
