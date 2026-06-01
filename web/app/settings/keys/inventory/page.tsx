"use client";

/**
 * Workspace credential ownership inventory.
 *
 * Lists every live personal access token in the current workspace
 * with the contact email of the human who owns it, so a SOC2 /
 * ISO 27001 reviewer can answer the standard question: "who do we
 * page if any of these credentials is leaked?" Tokens without an
 * owner are surfaced at the top so they get an owner attached
 * before the next compliance review.
 *
 * Admin role only. The owner-email field is set when a PAT is
 * minted from /settings/keys, or after the fact via the per-row
 * dialog on this page (admin role plus MFA step-up).
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  CheckCircle,
  Warning,
  At,
  Key,
  PencilSimple,
  Note,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface InventoryRow {
  id: string;
  name: string;
  roles: string[];
  owner_email: string;
  has_owner: boolean;
  description: string;
  has_description: boolean;
  created_at: number;
  last_used_at: number;
  expires_at: number;
  expired: boolean;
}

interface InventoryResponse {
  total: number;
  with_owner: number;
  without_owner: number;
  with_description: number;
  without_description: number;
  rows: InventoryRow[];
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: InventoryResponse }
  | { kind: "error"; status: number; message: string };

function authHeaders(): Record<string, string> {
  const k = getApiKey();
  return k ? { "X-API-Key": k } : {};
}

function fmtTs(ts: number): string {
  if (!ts) return "never";
  return new Date(ts * 1000).toLocaleString();
}

function rolesLabel(roles: string[]): string {
  return roles.length ? roles.join(", ") : "reader";
}

export default function KeysInventoryPage() {
  useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [editing, setEditing] = useState<InventoryRow | null>(null);
  const [editMode, setEditMode] = useState<"owner" | "description">("owner");
  const [draftEmail, setDraftEmail] = useState<string>("");
  const [draftDescription, setDraftDescription] = useState<string>("");
  const [saving, setSaving] = useState<boolean>(false);
  const [saveError, setSaveError] = useState<string>("");

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/admin/keys/inventory", {
        headers: authHeaders(),
      });
      if (!r.ok) {
        setState({
          kind: "error",
          status: r.status,
          message: await r.text(),
        });
        return;
      }
      const data: InventoryResponse = await r.json();
      setState({ kind: "ready", data });
    } catch (err) {
      setState({
        kind: "error",
        status: 0,
        message: err instanceof Error ? err.message : "network error",
      });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function openEdit(row: InventoryRow) {
    setEditing(row);
    setEditMode("owner");
    setDraftEmail(row.owner_email);
    setDraftDescription(row.description);
    setSaveError("");
  }

  function openEditDescription(row: InventoryRow) {
    setEditing(row);
    setEditMode("description");
    setDraftEmail(row.owner_email);
    setDraftDescription(row.description);
    setSaveError("");
  }

  function closeEdit() {
    setEditing(null);
    setDraftEmail("");
    setDraftDescription("");
    setSaveError("");
    setSaving(false);
  }

  async function save() {
    if (!editing) return;
    setSaving(true);
    setSaveError("");
    try {
      const isOwner = editMode === "owner";
      const url = isOwner
        ? `/api/admin/keys/${encodeURIComponent(editing.id)}/owner-email`
        : `/api/admin/keys/${encodeURIComponent(editing.id)}/description`;
      const payload = isOwner
        ? { owner_email: draftEmail }
        : { description: draftDescription };
      const r = await fetch(url, {
        method: "PUT",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        let msg = await r.text();
        try {
          const j = JSON.parse(msg);
          msg = j?.detail?.message || j?.detail || msg;
        } catch {
          /* keep raw */
        }
        setSaveError(typeof msg === "string" ? msg : JSON.stringify(msg));
        setSaving(false);
        return;
      }
      closeEdit();
      await load();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "network error");
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-10">
      <div className="mb-6 flex items-center justify-between">
        <Link
          href="/settings/keys"
          className="inline-flex items-center gap-2 text-sm text-zinc-500 hover:text-zinc-200"
        >
          <ArrowLeft size={16} weight="duotone" /> back to keys
        </Link>
        <span className="text-xs uppercase tracking-wider text-zinc-500">
          admin only
        </span>
      </div>

      <h1 className="mb-1 flex items-center gap-2 text-2xl font-medium">
        <Key size={22} weight="duotone" /> credential inventory
      </h1>
      <p className="mb-6 max-w-prose text-sm text-zinc-400">
        Every live personal access token in this workspace and the
        contact for the human who owns it. Tokens without an owner are
        listed first so they can be assigned before the next audit.
      </p>

      {state.kind === "loading" && <InventorySkeleton />}

      {state.kind === "error" && (
        <div className="rounded-md border border-red-900/60 bg-red-950/30 p-4 text-sm text-red-200">
          <div className="mb-1 flex items-center gap-2 font-medium">
            <Warning size={16} weight="duotone" /> could not load inventory
          </div>
          <div className="font-mono text-xs opacity-80">
            {state.status ? `${state.status} ` : ""}
            {state.message || "unknown error"}
          </div>
        </div>
      )}

      {state.kind === "ready" && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
            <Stat label="total" value={state.data.total} />
            <Stat label="with owner" value={state.data.with_owner} good />
            <Stat
              label="without owner"
              value={state.data.without_owner}
              bad={state.data.without_owner > 0}
            />
            <Stat
              label="documented"
              value={state.data.with_description}
              good
            />
            <Stat
              label="undocumented"
              value={state.data.without_description}
              bad={state.data.without_description > 0}
            />
          </div>

          {state.data.rows.length === 0 ? (
            <EmptyState />
          ) : (
            <div className="overflow-hidden rounded-lg border border-zinc-800">
              <table className="w-full text-left text-sm">
                <thead className="bg-zinc-900/50 text-xs uppercase tracking-wider text-zinc-500">
                  <tr>
                    <th className="px-4 py-2">name</th>
                    <th className="px-4 py-2">roles</th>
                    <th className="px-4 py-2">owner</th>
                    <th className="px-4 py-2">purpose</th>
                    <th className="px-4 py-2">last used</th>
                    <th className="px-4 py-2 text-right" />
                  </tr>
                </thead>
                <tbody>
                  {state.data.rows.map((row) => (
                    <tr
                      key={row.id}
                      className="border-t border-zinc-800/60 hover:bg-zinc-900/30"
                    >
                      <td className="px-4 py-3">
                        <div className="font-medium text-zinc-100">
                          {row.name}
                        </div>
                        <div className="font-mono text-xs text-zinc-500">
                          {row.id}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-zinc-300">
                        {rolesLabel(row.roles)}
                      </td>
                      <td className="px-4 py-3">
                        {row.has_owner ? (
                          <span className="inline-flex items-center gap-1.5 text-zinc-200">
                            <At size={14} weight="duotone" />
                            {row.owner_email}
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1.5 rounded bg-amber-950/40 px-2 py-0.5 text-xs font-medium text-amber-300">
                            <Warning size={12} weight="duotone" />
                            no owner
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {row.has_description ? (
                          <span
                            className="line-clamp-2 max-w-xs text-xs text-zinc-300"
                            title={row.description}
                          >
                            {row.description}
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1.5 rounded bg-amber-950/40 px-2 py-0.5 text-xs font-medium text-amber-300">
                            <Warning size={12} weight="duotone" />
                            undocumented
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-zinc-400">
                        {fmtTs(row.last_used_at)}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <div className="inline-flex flex-col items-end gap-1">
                          <button
                            type="button"
                            onClick={() => openEdit(row)}
                            className="inline-flex items-center gap-1.5 rounded border border-zinc-700 px-2 py-1 text-xs text-zinc-300 hover:border-zinc-500 hover:text-zinc-100"
                          >
                            <PencilSimple size={12} weight="duotone" />
                            set owner
                          </button>
                          <button
                            type="button"
                            onClick={() => openEditDescription(row)}
                            className="inline-flex items-center gap-1.5 rounded border border-zinc-700 px-2 py-1 text-xs text-zinc-300 hover:border-zinc-500 hover:text-zinc-100"
                          >
                            <Note size={12} weight="duotone" />
                            set purpose
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {editing && (
        <div
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-30 flex items-center justify-center bg-black/60 px-4"
          onClick={(e) => {
            if (e.target === e.currentTarget) closeEdit();
          }}
        >
          <div className="w-full max-w-md rounded-lg border border-zinc-800 bg-zinc-950 p-5 shadow-xl">
            <h2 className="mb-1 text-lg font-medium">
              {editMode === "owner" ? "set owner email" : "set purpose"}
            </h2>
            <p className="mb-3 text-xs text-zinc-500">
              token{" "}
              <span className="font-mono text-zinc-300">{editing.name}</span>{" "}
              ({editing.id})
            </p>
            {editMode === "owner" ? (
              <>
                <label
                  htmlFor="owner-email"
                  className="mb-1 block text-xs uppercase tracking-wider text-zinc-500"
                >
                  contact email
                </label>
                <input
                  id="owner-email"
                  type="email"
                  value={draftEmail}
                  onChange={(e) => setDraftEmail(e.target.value)}
                  placeholder="oncall@example.com"
                  className="w-full rounded border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-zinc-500"
                  autoFocus
                />
                <p className="mt-1 text-xs text-zinc-500">
                  leave blank to clear. admin role plus MFA step-up.
                </p>
              </>
            ) : (
              <>
                <label
                  htmlFor="purpose-note"
                  className="mb-1 block text-xs uppercase tracking-wider text-zinc-500"
                >
                  purpose / runbook note
                </label>
                <textarea
                  id="purpose-note"
                  value={draftDescription}
                  onChange={(e) => setDraftDescription(e.target.value)}
                  placeholder="CI deploy bot, owned by platform-eng"
                  rows={3}
                  maxLength={200}
                  className="w-full rounded border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-zinc-500"
                  autoFocus
                />
                <p className="mt-1 flex items-center justify-between text-xs text-zinc-500">
                  <span>leave blank to clear. admin role plus MFA step-up.</span>
                  <span className="tabular-nums">
                    {draftDescription.length}/200
                  </span>
                </p>
              </>
            )}
            {saveError && (
              <div className="mt-3 rounded border border-red-900/60 bg-red-950/30 p-2 text-xs text-red-200">
                {saveError}
              </div>
            )}
            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={closeEdit}
                className="rounded px-3 py-1.5 text-sm text-zinc-400 hover:text-zinc-200"
                disabled={saving}
              >
                cancel
              </button>
              <button
                type="button"
                onClick={() => void save()}
                disabled={saving}
                className="inline-flex items-center gap-1.5 rounded bg-zinc-100 px-3 py-1.5 text-sm font-medium text-zinc-900 hover:bg-white disabled:opacity-50"
              >
                {saving ? "saving" : "save"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  good,
  bad,
}: {
  label: string;
  value: number;
  good?: boolean;
  bad?: boolean;
}) {
  const tone = bad
    ? "border-amber-900/60 bg-amber-950/20 text-amber-200"
    : good
      ? "border-emerald-900/60 bg-emerald-950/20 text-emerald-200"
      : "border-zinc-800 bg-zinc-900/40 text-zinc-200";
  return (
    <div className={`rounded-lg border p-3 ${tone}`}>
      <div className="text-xs uppercase tracking-wider opacity-70">{label}</div>
      <div className="mt-1 text-2xl font-medium tabular-nums">{value}</div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="rounded-lg border border-dashed border-zinc-800 p-8 text-center">
      <CheckCircle
        size={28}
        weight="duotone"
        className="mx-auto mb-2 text-emerald-400"
      />
      <p className="text-sm text-zinc-300">no live personal access tokens</p>
      <p className="mt-1 text-xs text-zinc-500">
        mint one from{" "}
        <Link href="/settings/keys" className="underline">
          /settings/keys
        </Link>{" "}
        and the inventory will appear here.
      </p>
    </div>
  );
}

function InventorySkeleton() {
  return (
    <div className="space-y-3" aria-busy>
      <div className="grid grid-cols-3 gap-3">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="h-16 animate-pulse rounded-lg border border-zinc-800 bg-zinc-900/40"
          />
        ))}
      </div>
      <div className="h-64 animate-pulse rounded-lg border border-zinc-800 bg-zinc-900/30" />
    </div>
  );
}
