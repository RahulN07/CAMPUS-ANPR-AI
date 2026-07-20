const STYLES = {
  AUTHORIZED: "bg-emerald-50 text-emerald-600 ring-emerald-600/20",
  ACTIVE: "bg-emerald-50 text-emerald-600 ring-emerald-600/20",
  ENTRY: "bg-emerald-50 text-emerald-600 ring-emerald-600/20",
  UNAUTHORIZED: "bg-red-50 text-red-600 ring-red-600/20",
  INACTIVE: "bg-red-50 text-red-600 ring-red-600/20",
  EXIT: "bg-red-50 text-red-600 ring-red-600/20",
  EXPIRED: "bg-orange-50 text-orange-600 ring-orange-600/20",
  PENDING: "bg-amber-50 text-amber-600 ring-amber-600/20",
  DEFAULT: "bg-slate-100 text-slate-500 ring-slate-500/20",
};

const LABELS = {
  AUTHORIZED: "Authorized",
  UNAUTHORIZED: "Unauthorized",
  EXPIRED: "Expired",
  PENDING: "Pending Review",
  ACTIVE: "Active",
  INACTIVE: "Inactive",
  ENTRY: "Entry",
  EXIT: "Exit",
};

export default function StatusBadge({ status, label }) {
  const key = (status || "").toString().toUpperCase();
  const style = STYLES[key] || STYLES.DEFAULT;

  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold ring-1 ring-inset whitespace-nowrap ${style}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {label || LABELS[key] || status || "—"}
    </span>
  );
}
