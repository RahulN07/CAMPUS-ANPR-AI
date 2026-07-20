import { LuTriangleAlert } from "react-icons/lu";

export default function ConfirmDialog({
  open,
  title = "Are you sure?",
  message,
  confirmLabel = "Delete",
  cancelLabel = "Cancel",
  danger = true,
  loading = false,
  onConfirm,
  onCancel,
}) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink-950/50 p-4 animate-fadeIn">
      <div className="bg-white rounded-2xl shadow-card w-full max-w-sm p-6">
        <div className="flex items-start gap-3">
          <div
            className={`h-10 w-10 rounded-xl grid place-items-center shrink-0 ${
              danger ? "bg-red-50 text-red-500" : "bg-brand-50 text-brand-600"
            }`}
          >
            <LuTriangleAlert size={20} />
          </div>
          <div>
            <h3 className="font-display font-semibold text-ink-950">{title}</h3>
            {message && <p className="text-sm text-slate-500 mt-1">{message}</p>}
          </div>
        </div>

        <div className="flex justify-end gap-2 mt-6">
          <button
            onClick={onCancel}
            className="px-4 py-2 rounded-lg text-sm font-medium text-slate-600 hover:bg-slate-100"
          >
            {cancelLabel}
          </button>
          <button
            onClick={onConfirm}
            disabled={loading}
            className={`px-4 py-2 rounded-lg text-sm font-medium text-white disabled:opacity-60 ${
              danger ? "bg-red-600 hover:bg-red-700" : "bg-brand-600 hover:bg-brand-700"
            }`}
          >
            {loading ? "Please wait…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
