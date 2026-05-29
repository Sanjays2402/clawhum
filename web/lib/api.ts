export const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";
export async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(API_BASE + path, init);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json() as Promise<T>;
}
