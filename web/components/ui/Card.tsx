export default function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <div className={`bg-[var(--panel)] border border-[var(--line)] rounded-xl ${className}`}>{children}</div>;
}
