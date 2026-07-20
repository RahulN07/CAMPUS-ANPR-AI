export function Spinner({ size = 20, className = "" }) {
  return (
    <svg
      className={`animate-spin text-brand-600 ${className}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
    >
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" opacity="0.2" />
      <path d="M22 12a10 10 0 0 0-10-10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export function PageLoader({ label = "Loading…" }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-24 text-slate-400">
      <Spinner size={28} />
      <p className="text-sm">{label}</p>
    </div>
  );
}

export function SkeletonRow({ cols = 5 }) {
  return (
    <tr className="animate-pulse">
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i} className="px-4 py-4">
          <div className="h-3.5 bg-slate-100 rounded w-full max-w-[120px]" />
        </td>
      ))}
    </tr>
  );
}
