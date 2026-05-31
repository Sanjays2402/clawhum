// Test helper: shim window + localStorage for libs that touch them at module load.
class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string) {
    return this.m.has(k) ? (this.m.get(k) as string) : null;
  }
  setItem(k: string, v: string) {
    this.m.set(k, String(v));
  }
  removeItem(k: string) {
    this.m.delete(k);
  }
  clear() {
    this.m.clear();
  }
}

const g = globalThis as any;
if (!g.window) g.window = {};
if (!g.window.localStorage) g.window.localStorage = new MemStorage();
if (!g.window.dispatchEvent) g.window.dispatchEvent = () => true;
if (!g.window.addEventListener) g.window.addEventListener = () => {};
if (!g.window.removeEventListener) g.window.removeEventListener = () => {};
// Also surface on globalThis so module code that references window.localStorage
// without an explicit window prefix still finds it.
if (!g.localStorage) g.localStorage = g.window.localStorage;

export const memWindow = g.window as {
  localStorage: MemStorage;
  dispatchEvent: () => boolean;
  addEventListener: () => void;
  removeEventListener: () => void;
};
