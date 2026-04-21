import { useQuery } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";

type Props = { invariant: string; onClose: () => void };

type RunbookResponse = {
  invariant: string;
  display_name: string;
  severity: string;
  markdown: string;
};

async function fetchRunbook(invariant: string): Promise<RunbookResponse> {
  const r = await fetch(`/api/admin/substrate/runbook/${invariant}`, {
    credentials: "include",
  });
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try {
      const body = await r.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* body not JSON */
    }
    throw new Error(detail);
  }
  return r.json();
}

export default function RunbookDrawer({ invariant, onClose }: Props) {
  const { data, error, isLoading } = useQuery<RunbookResponse, Error>({
    queryKey: ["substrate-runbook", invariant],
    queryFn: () => fetchRunbook(invariant),
    staleTime: 5 * 60 * 1000,
  });

  const deepLink = `${window.location.origin}/admin/substrate/runbook/${invariant}`;

  return (
    <aside className="fixed right-0 top-0 h-full w-[720px] max-w-[95vw] bg-white/5 backdrop-blur-lg border-l border-white/10 z-50 overflow-y-auto p-6 text-white">
      <header className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-semibold">{data?.display_name ?? invariant}</h2>
          <p className="text-xs text-white/60">{data?.severity ?? ""} · {invariant}</p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            className="px-3 py-1 rounded bg-white/10 hover:bg-white/20 text-sm"
            onClick={() => navigator.clipboard.writeText(deepLink)}
          >Copy link</button>
          <button
            type="button"
            className="px-3 py-1 rounded bg-white/10 hover:bg-white/20 text-sm"
            onClick={onClose}
          >Close</button>
        </div>
      </header>
      {isLoading && <p className="text-white/70">Loading…</p>}
      {error && <p className="text-red-300">{error.message}</p>}
      {data && (
        <article className="prose prose-invert max-w-none">
          <ReactMarkdown rehypePlugins={[rehypeSanitize]}>
            {data.markdown}
          </ReactMarkdown>
        </article>
      )}
    </aside>
  );
}
