export function Label({ children, required }) {
  return (
    <label className="block text-xs font-semibold text-slate-600 mb-1.5">
      {children} {required && <span className="text-red-500">*</span>}
    </label>
  );
}

const baseInput =
  "w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm text-ink-950 placeholder:text-slate-400 focus-ring transition-shadow";

export function TextField({ label, required, className = "", error, ...props }) {
  return (
    <div className={className}>
      {label && <Label required={required}>{label}</Label>}
      <input
        className={`${baseInput} ${error ? "border-red-300 focus:ring-red-200" : ""}`}
        {...props}
      />
    </div>
  );
}

export function SelectField({ label, required, options = [], className = "", placeholder, ...props }) {
  return (
    <div className={className}>
      {label && <Label required={required}>{label}</Label>}
      <select className={`${baseInput} bg-white`} {...props}>
        {placeholder && <option value="">{placeholder}</option>}
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    </div>
  );
}

export function TextAreaField({ label, required, className = "", ...props }) {
  return (
    <div className={className}>
      {label && <Label required={required}>{label}</Label>}
      <textarea className={`${baseInput} min-h-[90px] resize-y`} {...props} />
    </div>
  );
}
