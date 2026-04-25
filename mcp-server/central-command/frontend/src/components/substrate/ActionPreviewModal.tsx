import { useState } from "react";
import { csrfHeaders } from "../../utils/csrf";

type Props = {
  actionKey: string;
  requiredReasonChars: number;
  plan: string;
  targetRef: Record<string, unknown>;
  cliFallback?: string;
  onClose: () => void;
  onDone: (actionId: string) => void;
};

export default function ActionPreviewModal(p: Props) {
  const [reason, setReason] = useState("");
  const [initials, setInitials] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{ action_id: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const canSubmit =
    !submitting &&
    initials.trim().length >= 2 &&
    initials.trim().length <= 4 &&
    reason.trim().length >= p.requiredReasonChars;

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const actionHeaders: Record<string, string> = {
        "Content-Type": "application/json",
        "Idempotency-Key": `${p.actionKey}-${Date.now()}-${initials}`,
        ...csrfHeaders(),
      };
      const r = await fetch("/api/admin/substrate/action", {
        method: "POST",
        credentials: "include",
        // headers contain X-CSRF-Token via csrfHeaders()
        headers: actionHeaders,
        body: JSON.stringify({
          action_key: p.actionKey,
          target_ref: p.targetRef,
          reason: `[${initials.toUpperCase()}] ${reason}`.trim(),
        }),
      });
      if (!r.ok) {
        let detail = `HTTP ${r.status}`;
        try {
          const body = await r.json();
          if (body?.detail) detail = String(body.detail);
        } catch {
          /* non-JSON body */
        }
        throw new Error(detail);
      }
      const resp = (await r.json()) as { action_id: string; status: string };
      setResult({ action_id: resp.action_id });
      p.onDone(resp.action_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center">
      <div className="bg-slate-900 border border-white/10 rounded-lg w-[560px] max-w-[92vw] p-6 text-white">
        <h2 className="text-lg font-semibold mb-2">Preview: {p.actionKey}</h2>
        <p className="text-sm text-white/80 mb-4 whitespace-pre-wrap">{p.plan}</p>

        {p.requiredReasonChars > 0 && (
          <label className="block mb-3 text-sm">
            Reason (min {p.requiredReasonChars} chars)
            <textarea
              aria-label="reason"
              className="mt-1 w-full p-2 bg-white/5 rounded border border-white/10"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
            />
            <span className="text-xs text-white/50">{reason.length} chars</span>
          </label>
        )}

        <label className="block mb-4 text-sm">
          Your initials (2–4 chars, saved to audit log)
          <input
            aria-label="initials"
            className="mt-1 w-24 p-2 bg-white/5 rounded border border-white/10"
            value={initials}
            onChange={(e) => setInitials(e.target.value.slice(0, 4))}
          />
        </label>

        {result && (
          <p className="mb-3 text-green-300 text-sm">
            Done — action_id {result.action_id}
          </p>
        )}
        {error && (
          <div className="mb-3 text-sm">
            <p className="text-red-300">{error}</p>
            {p.cliFallback && (
              <pre className="mt-2 text-xs bg-black/50 p-2 rounded whitespace-pre-wrap">
                {p.cliFallback}
              </pre>
            )}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={p.onClose}
            className="px-3 py-1.5 rounded bg-white/10 hover:bg-white/20"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!canSubmit}
            className="px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40"
          >Confirm</button>
        </div>
      </div>
    </div>
  );
}
