import { ReactNode, useEffect } from "react";

/** Modal confirmation shown before any destructive action. Esc / Cancel / backdrop click = no-op. */
export default function ConfirmDialog({
  title, children, confirmLabel = "Delete", onConfirm, onCancel,
}: { title: string; children: ReactNode; confirmLabel?: string; onConfirm: () => void; onCancel: () => void }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onCancel]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onCancel} role="presentation">
      <div role="alertdialog" aria-modal="true" aria-labelledby="confirm-title"
        className="w-full max-w-md rounded-xl border border-slate-700 bg-slate-900 p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <h2 id="confirm-title" className="text-base font-semibold text-slate-100">{title}</h2>
        <div className="mt-2 space-y-2 text-sm text-slate-300">{children}</div>
        <div className="mt-5 flex justify-end gap-2">
          <button autoFocus className="rounded border border-slate-600 px-3 py-1.5 text-sm hover:bg-slate-800" onClick={onCancel}>Cancel</button>
          <button className="rounded bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-500" onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}
