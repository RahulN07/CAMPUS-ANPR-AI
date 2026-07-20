import { LuInbox } from "react-icons/lu";

export default function EmptyState({ icon: Icon = LuInbox, title = "Nothing here yet", message, action }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-16 px-6">
      <div className="h-14 w-14 rounded-2xl bg-slate-100 grid place-items-center text-slate-400 mb-4">
        <Icon size={26} />
      </div>
      <h3 className="font-display font-semibold text-ink-950">{title}</h3>
      {message && <p className="text-sm text-slate-500 mt-1 max-w-xs">{message}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
