import { LuArrowUpRight, LuArrowDownRight } from "react-icons/lu";

const TONES = {
  blue: "from-brand-400 to-brand-600",
  green: "from-emerald-400 to-emerald-600",
  red: "from-red-400 to-red-600",
  amber: "from-amber-400 to-amber-600",
  purple: "from-violet-400 to-violet-600",
  cyan: "from-cyan-400 to-cyan-600",
  pink: "from-pink-400 to-pink-600",
};

export default function StatCard({ title, value, icon: Icon, tone = "blue", trend, trendLabel }) {
  const isUp = typeof trend === "number" && trend >= 0;

  return (
    <div className="bg-white rounded-2xl shadow-card border border-slate-100 p-5 flex flex-col gap-4">
      <div className="flex items-start justify-between">
        <p className="text-[13px] font-medium text-slate-500">{title}</p>
        {Icon && (
          <div className={`h-10 w-10 rounded-xl bg-gradient-to-br ${TONES[tone]} grid place-items-center shadow-soft shrink-0`}>
            <Icon size={18} className="text-white" />
          </div>
        )}
      </div>
      <div className="flex items-end justify-between">
        <h2 className="text-2xl sm:text-3xl font-display font-bold text-ink-950">{value}</h2>
        {typeof trend === "number" && (
          <span
            className={`inline-flex items-center gap-1 text-xs font-semibold ${
              isUp ? "text-emerald-600" : "text-red-500"
            }`}
          >
            {isUp ? <LuArrowUpRight size={14} /> : <LuArrowDownRight size={14} />}
            {Math.abs(trend)}%
          </span>
        )}
      </div>
      {trendLabel && <p className="text-[11px] text-slate-400 -mt-2">{trendLabel}</p>}
    </div>
  );
}
