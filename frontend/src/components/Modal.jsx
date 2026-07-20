import { LuX } from "react-icons/lu";

export default function Modal({
  open,
  title,
  subtitle,
  onClose,
  children,
  width = "max-w-2xl",
}) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-ink-950/60 p-4">
      <div className="flex min-h-full items-start justify-center py-6">
        <div
          className={`w-full ${width} overflow-hidden rounded-2xl bg-white shadow-2xl`}
        >
          {/* Header */}
          <div className="sticky top-0 z-10 flex items-start justify-between border-b border-slate-200 bg-white px-6 py-5">
            <div>
              <h3 className="font-display text-lg font-semibold text-ink-950">
                {title}
              </h3>

              {subtitle && (
                <p className="mt-1 text-sm text-slate-500">
                  {subtitle}
                </p>
              )}
            </div>

            <button
              onClick={onClose}
              className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-600"
            >
              <LuX size={18} />
            </button>
          </div>

          {/* Body */}
          <div className="max-h-[calc(100vh-140px)] overflow-y-auto px-6 py-5">
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}