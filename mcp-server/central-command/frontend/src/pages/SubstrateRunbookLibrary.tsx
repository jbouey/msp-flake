import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import RunbookDrawer from "../components/substrate/RunbookDrawer";

type Item = {
  invariant: string;
  display_name: string;
  severity: string;
  has_action: boolean;
  action_key: string | null;
};

async function fetchRunbookIndex(): Promise<{ items: Item[] }> {
  const r = await fetch("/api/admin/substrate/runbooks", { credentials: "include" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export default function SubstrateRunbookLibrary() {
  const [onlyAction, setOnlyAction] = useState(false);
  const [severity, setSeverity] = useState<string>("all");
  const [drawer, setDrawer] = useState<string | null>(null);

  const { data, error, isLoading } = useQuery<{ items: Item[] }, Error>({
    queryKey: ["substrate-runbooks-index"],
    queryFn: fetchRunbookIndex,
  });

  const items = (data?.items ?? [])
    .filter((i) => !onlyAction || i.has_action)
    .filter((i) => severity === "all" || i.severity === severity);

  return (
    <div className="p-6 text-white">
      <h1 className="text-2xl font-semibold mb-2">Substrate Runbook Library</h1>
      <p className="text-white/60 text-sm mb-4">
        Every invariant the Substrate Integrity Engine asserts. Click any row for the runbook.
      </p>

      <div className="flex gap-4 mb-4 text-sm items-center">
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={onlyAction}
            onChange={(e) => setOnlyAction(e.target.checked)}
            aria-label="only with action"
          />
          Only with action
        </label>
        <select
          value={severity}
          onChange={(e) => setSeverity(e.target.value)}
          aria-label="severity filter"
          className="bg-slate-800 border border-white/10 rounded px-2 py-1"
        >
          <option value="all">All severities</option>
          <option value="sev1">sev1</option>
          <option value="sev2">sev2</option>
          <option value="sev3">sev3</option>
        </select>
      </div>

      {isLoading && <p className="text-white/60 text-sm">Loading…</p>}
      {error && <p className="text-red-300 text-sm">{error.message}</p>}

      {data && (
        <table className="w-full text-sm">
          <thead className="text-white/60 text-left">
            <tr>
              <th className="p-2">Invariant</th>
              <th className="p-2">Severity</th>
              <th className="p-2">Action?</th>
              <th className="p-2" />
            </tr>
          </thead>
          <tbody>
            {items.map((i) => (
              <tr key={i.invariant} className="border-t border-white/10">
                <td className="p-2">
                  <div className="text-white">{i.display_name}</div>
                  <div className="text-xs text-white/50 font-mono">{i.invariant}</div>
                </td>
                <td className="p-2">{i.severity}</td>
                <td className="p-2">{i.has_action ? i.action_key : "—"}</td>
                <td className="p-2">
                  <button
                    type="button"
                    className="px-2 py-1 text-xs rounded bg-white/10 hover:bg-white/20"
                    onClick={() => setDrawer(i.invariant)}
                  >View runbook</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {drawer && <RunbookDrawer invariant={drawer} onClose={() => setDrawer(null)} />}
    </div>
  );
}
