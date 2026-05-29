export default function Button(props: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      className={`px-4 py-2 rounded bg-[var(--accent)] text-black font-medium disabled:opacity-50 ${props.className ?? ""}`}
    />
  );
}
