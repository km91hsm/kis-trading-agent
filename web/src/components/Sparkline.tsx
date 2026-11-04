// 작은 추이선 — 인라인 SVG polyline. 위가 큰 값. 값이 하나뿐이거나 모두 같으면 가운데 수평선.
export function Sparkline({ values, width = 80, height = 24 }: { values: number[]; width?: number; height?: number }) {
  const n = values.length;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min;
  const r = (v: number) => Math.round(v * 100) / 100;
  const points = values
    .map((v, i) => {
      const x = n > 1 ? (i / (n - 1)) * width : width / 2;
      const y = span > 0 ? height - ((v - min) / span) * height : height / 2;
      return `${r(x)},${r(y)}`;
    })
    .join(" ");
  return (
    <svg className="sparkline" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true" focusable="false">
      {n ? <polyline points={points} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" /> : null}
    </svg>
  );
}
