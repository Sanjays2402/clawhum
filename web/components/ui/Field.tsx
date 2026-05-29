export default function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-[var(--muted)] uppercase tracking-wide text-xs">{label}</span>
      {children}
    </label>
  );
}
