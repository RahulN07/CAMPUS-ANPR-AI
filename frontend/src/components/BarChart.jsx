/**
 * Minimal dependency-free grouped bar chart, rendered as SVG.
 * data: [{ label, a, b }], where `a` and `b` are two comparable series
 * (e.g. entries vs exits per day).
 */
export default function BarChart({ data, seriesA = "Entries", seriesB = "Exits", colorA = "#3763F4", colorB = "#22C55E" }) {
  if (!data.length) return null;

  const max = Math.max(1, ...data.flatMap((d) => [d.a, d.b]));
  const width = 640;
  const height = 220;
  const padding = { top: 10, right: 10, bottom: 28, left: 30 };
  const chartW = width - padding.left - padding.right;
  const chartH = height - padding.top - padding.bottom;
  const groupW = chartW / data.length;
  const barW = Math.min(22, groupW / 3);

  return (
    <div className="w-full overflow-x-auto">
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full min-w-[480px]" role="img">
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <line
            key={t}
            x1={padding.left}
            x2={width - padding.right}
            y1={padding.top + chartH * (1 - t)}
            y2={padding.top + chartH * (1 - t)}
            stroke="#EEF1F7"
          />
        ))}

        {data.map((d, i) => {
          const groupX = padding.left + i * groupW;
          const hA = (d.a / max) * chartH;
          const hB = (d.b / max) * chartH;
          const cx = groupX + groupW / 2;
          return (
            <g key={d.label}>
              <rect
                x={cx - barW - 2}
                y={padding.top + chartH - hA}
                width={barW}
                height={hA}
                rx={3}
                fill={colorA}
              />
              <rect
                x={cx + 2}
                y={padding.top + chartH - hB}
                width={barW}
                height={hB}
                rx={3}
                fill={colorB}
              />
              <text
                x={cx}
                y={height - 8}
                textAnchor="middle"
                fontSize="10"
                fill="#94A3B8"
              >
                {d.label}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="flex items-center gap-4 justify-center mt-1">
        <Legend color={colorA} label={seriesA} />
        <Legend color={colorB} label={seriesB} />
      </div>
    </div>
  );
}

function Legend({ color, label }) {
  return (
    <div className="flex items-center gap-1.5 text-xs text-slate-500">
      <span className="h-2.5 w-2.5 rounded-sm" style={{ background: color }} />
      {label}
    </div>
  );
}
