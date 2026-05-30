// Minimal Prometheus text-format parser. Handles HELP/TYPE comments and bare samples.
export interface PromSample {
  name: string;
  labels: Record<string, string>;
  value: number;
}

export interface PromMetric {
  name: string;
  help?: string;
  type?: string; // counter | gauge | histogram | summary | untyped
  samples: PromSample[];
}

export function parseProm(text: string): PromMetric[] {
  const metrics = new Map<string, PromMetric>();
  const lines = text.split(/\r?\n/);
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    if (line.startsWith("#")) {
      const m = line.match(/^#\s+(HELP|TYPE)\s+(\S+)\s+(.+)$/);
      if (!m) continue;
      const [, kind, name, rest] = m;
      const entry = metrics.get(name) || { name, samples: [] };
      if (kind === "HELP") entry.help = rest;
      else entry.type = rest.trim();
      metrics.set(name, entry);
      continue;
    }
    // sample: name{labels} value [ts]
    const m = line.match(/^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+([-+]?[\d.eE+]+|NaN|\+?Inf|-Inf)/);
    if (!m) continue;
    const [, name, labelStr, valStr] = m;
    const labels: Record<string, string> = {};
    if (labelStr) {
      const inner = labelStr.slice(1, -1);
      for (const part of inner.split(",")) {
        const kv = part.match(/^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"((?:[^"\\]|\\.)*)"\s*$/);
        if (kv) labels[kv[1]] = kv[2].replace(/\\"/g, '"').replace(/\\\\/g, "\\").replace(/\\n/g, "\n");
      }
    }
    const value = valStr === "NaN" ? NaN : valStr === "+Inf" || valStr === "Inf" ? Infinity : valStr === "-Inf" ? -Infinity : Number(valStr);
    const entry = metrics.get(name) || { name, samples: [] };
    entry.samples.push({ name, labels, value });
    metrics.set(name, entry);
  }
  return Array.from(metrics.values()).sort((a, b) => a.name.localeCompare(b.name));
}
