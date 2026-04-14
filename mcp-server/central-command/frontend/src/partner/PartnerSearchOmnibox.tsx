/**
 * PartnerSearchOmnibox — Session 206 round-table P1.
 *
 * Cmd-K / Ctrl-K fuzzy search across a partner's book of business:
 * sites, 7-day incidents, promoted rules. Keyboard-first.
 *
 * Scoping is enforced server-side (partner_id isolation on
 * /api/partners/me/search). This component only renders what the
 * server returns.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

interface Hit {
  kind: 'site' | 'incident' | 'rule';
  title: string;
  subtitle: string;
  href: string;
}

const KIND_LABEL: Record<Hit['kind'], string> = {
  site: 'Sites',
  incident: 'Incidents (7d)',
  rule: 'Rules',
};

const KIND_ICON: Record<Hit['kind'], string> = {
  site: '🏥',
  incident: '⚠',
  rule: '⚙',
};

export const PartnerSearchOmnibox: React.FC = () => {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<Hit[]>([]);
  const [cursor, setCursor] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const openBox = useCallback(() => {
    setOpen(true);
    setCursor(0);
    setTimeout(() => inputRef.current?.focus(), 10);
  }, []);

  const closeBox = useCallback(() => {
    setOpen(false);
    setQ('');
    setHits([]);
    setError(null);
    abortRef.current?.abort();
  }, []);

  // Global Cmd-K / Ctrl-K listener
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        if (open) closeBox();
        else openBox();
      }
      if (e.key === 'Escape' && open) {
        e.preventDefault();
        closeBox();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, openBox, closeBox]);

  // Debounced fetch
  useEffect(() => {
    if (!open) return;
    const trimmed = q.trim();
    if (trimmed.length < 2) {
      setHits([]);
      setLoading(false);
      return;
    }
    const t = setTimeout(async () => {
      abortRef.current?.abort();
      const ctl = new AbortController();
      abortRef.current = ctl;
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`/api/partners/me/search?q=${encodeURIComponent(trimmed)}&limit=8`, {
          credentials: 'include',
          signal: ctl.signal,
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const body = (await res.json()) as { hits: Hit[]; query: string };
        setHits(body.hits || []);
        setCursor(0);
      } catch (e) {
        if ((e as Error).name !== 'AbortError') {
          setError(e instanceof Error ? e.message : String(e));
          setHits([]);
        }
      } finally {
        setLoading(false);
      }
    }, 180);
    return () => clearTimeout(t);
  }, [q, open]);

  const grouped = useMemo(() => {
    const groups: Record<Hit['kind'], Hit[]> = { site: [], incident: [], rule: [] };
    hits.forEach((h) => groups[h.kind].push(h));
    const ordered: Array<[Hit['kind'], Hit[]]> = [
      ['site', groups.site],
      ['incident', groups.incident],
      ['rule', groups.rule],
    ];
    return ordered.filter(([, xs]) => xs.length > 0);
  }, [hits]);

  const go = useCallback((hit: Hit) => {
    closeBox();
    navigate(hit.href);
  }, [closeBox, navigate]);

  const onInputKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setCursor((c) => Math.min(hits.length - 1, c + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setCursor((c) => Math.max(0, c - 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const hit = hits[cursor];
      if (hit) go(hit);
    }
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={openBox}
        className="fixed bottom-6 right-6 z-30 px-3 py-2 rounded-full bg-slate-900/90 backdrop-blur text-slate-200 text-xs border border-slate-700 shadow-lg hover:bg-slate-800 flex items-center gap-2"
        title="Open search (Cmd/Ctrl+K)"
      >
        <span>🔍</span>
        <span className="hidden sm:inline">Search</span>
        <kbd className="hidden sm:inline px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 text-[10px] font-mono border border-slate-700">⌘K</kbd>
      </button>
    );
  }

  // `flatIndex` lets us compute the current cursor's global position when
  // rendering hits in grouped sections — cursor is a flat index into `hits`.
  let flatIndex = -1;

  return (
    <div
      className="fixed inset-0 z-50 bg-black/50 flex items-start justify-center pt-20 px-4"
      onClick={(e) => { if (e.target === e.currentTarget) closeBox(); }}
    >
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-xl border border-slate-200 overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-100 flex items-center gap-2">
          <span className="text-slate-400 text-lg">🔍</span>
          <input
            ref={inputRef}
            type="text"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onInputKey}
            placeholder="Search sites, incidents, rules…"
            className="flex-1 text-sm bg-transparent outline-none placeholder-slate-400 text-slate-900"
          />
          <kbd className="px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 text-[10px] font-mono border border-slate-200">ESC</kbd>
        </div>

        <div className="max-h-96 overflow-y-auto">
          {loading && q.trim().length >= 2 && (
            <div className="px-4 py-3 text-xs text-slate-500">Searching…</div>
          )}
          {!loading && error && (
            <div className="px-4 py-3 text-xs text-rose-600">{error}</div>
          )}
          {!loading && !error && q.trim().length < 2 && (
            <div className="px-4 py-6 text-center text-xs text-slate-400">
              Type at least 2 characters.
              <div className="mt-2 text-[11px] text-slate-400">
                <kbd className="px-1 py-0.5 rounded bg-slate-100 border border-slate-200">↑</kbd>
                <kbd className="px-1 py-0.5 rounded bg-slate-100 border border-slate-200 ml-1">↓</kbd>
                to navigate,
                <kbd className="px-1 py-0.5 rounded bg-slate-100 border border-slate-200 ml-1">Enter</kbd>
                to open
              </div>
            </div>
          )}
          {!loading && !error && q.trim().length >= 2 && hits.length === 0 && (
            <div className="px-4 py-6 text-center text-xs text-slate-500">
              No results for "{q}"
            </div>
          )}
          {!loading && !error && grouped.map(([kind, items]) => (
            <div key={kind}>
              <div className="px-4 pt-2 pb-1 text-[10px] uppercase tracking-wide text-slate-500 font-semibold">
                {KIND_LABEL[kind]}
              </div>
              {items.map((h) => {
                flatIndex += 1;
                const isSelected = flatIndex === cursor;
                return (
                  <button
                    key={`${h.kind}:${h.href}:${flatIndex}`}
                    type="button"
                    onMouseEnter={() => setCursor(flatIndex)}
                    onClick={() => go(h)}
                    className={`w-full text-left px-4 py-2 flex items-start gap-3 transition-colors ${
                      isSelected ? 'bg-blue-50' : 'hover:bg-slate-50'
                    }`}
                  >
                    <span className="text-lg shrink-0 w-5 text-center">{KIND_ICON[h.kind]}</span>
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium text-slate-900 truncate">{h.title}</div>
                      <div className="text-[11px] text-slate-500 truncate">{h.subtitle}</div>
                    </div>
                    {isSelected && <span className="text-[10px] text-blue-500 font-mono shrink-0">↵</span>}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default PartnerSearchOmnibox;
