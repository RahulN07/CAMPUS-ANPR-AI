import { LuChevronLeft, LuChevronRight } from "react-icons/lu";

export default function Pagination({ page, pageSize, total, onPageChange }) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const start = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, total);

  function pageNumbers() {
    const pages = [];
    const window = 1;
    for (let p = 1; p <= totalPages; p++) {
      if (p === 1 || p === totalPages || Math.abs(p - page) <= window) {
        pages.push(p);
      } else if (pages[pages.length - 1] !== "…") {
        pages.push("…");
      }
    }
    return pages;
  }

  return (
    <div className="flex flex-col sm:flex-row items-center justify-between gap-3 px-1 py-3 text-sm">
      <p className="text-slate-500">
        Showing <span className="font-medium text-ink-950">{start}</span>–
        <span className="font-medium text-ink-950">{end}</span> of{" "}
        <span className="font-medium text-ink-950">{total}</span> entries
      </p>

      <div className="flex items-center gap-1">
        <button
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
          className="h-8 w-8 grid place-items-center rounded-lg border border-slate-200 text-slate-500 disabled:opacity-40 hover:bg-slate-50"
        >
          <LuChevronLeft size={16} />
        </button>

        {pageNumbers().map((p, idx) =>
          p === "…" ? (
            <span key={`e-${idx}`} className="px-2 text-slate-400">
              …
            </span>
          ) : (
            <button
              key={p}
              onClick={() => onPageChange(p)}
              className={`h-8 min-w-8 px-2 rounded-lg text-sm font-medium ${
                p === page
                  ? "bg-brand-600 text-white"
                  : "text-slate-600 border border-slate-200 hover:bg-slate-50"
              }`}
            >
              {p}
            </button>
          )
        )}

        <button
          disabled={page >= totalPages}
          onClick={() => onPageChange(page + 1)}
          className="h-8 w-8 grid place-items-center rounded-lg border border-slate-200 text-slate-500 disabled:opacity-40 hover:bg-slate-50"
        >
          <LuChevronRight size={16} />
        </button>
      </div>
    </div>
  );
}
