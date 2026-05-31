// Pure helpers backing the history page's bulk-select UX.
// Kept framework-free so they can be exercised under the node test runner.

export function toggleId(prev: ReadonlySet<string>, id: string): Set<string> {
  const next = new Set(prev);
  if (next.has(id)) next.delete(id); else next.add(id);
  return next;
}

export function toggleAll(prev: ReadonlySet<string>, visibleIds: readonly string[]): Set<string> {
  const next = new Set(prev);
  const allOn = visibleIds.length > 0 && visibleIds.every((id) => prev.has(id));
  if (allOn) {
    for (const id of visibleIds) next.delete(id);
  } else {
    for (const id of visibleIds) next.add(id);
  }
  return next;
}

export function pruneStale(prev: ReadonlySet<string>, visibleIds: readonly string[]): Set<string> {
  const visible = new Set(visibleIds);
  const next = new Set<string>();
  for (const id of prev) if (visible.has(id)) next.add(id);
  return next;
}

export interface SelectionSummary {
  count: number;
  allSelected: boolean;
  someSelected: boolean;
}

export function summarize(prev: ReadonlySet<string>, visibleIds: readonly string[]): SelectionSummary {
  const allSelected = visibleIds.length > 0 && visibleIds.every((id) => prev.has(id));
  const count = prev.size;
  return { count, allSelected, someSelected: count > 0 && !allSelected };
}

export function mergeTags(existing: readonly string[], incoming: readonly string[]): string[] {
  const norm = (s: string) => s.trim().toLowerCase();
  const out = new Set<string>();
  for (const t of existing) {
    const v = norm(t);
    if (v) out.add(v);
  }
  for (const t of incoming) {
    const v = norm(t);
    if (v) out.add(v);
  }
  return Array.from(out).sort();
}
