import React, { useState, useEffect } from 'react';
import { DocumentUpload } from './DocumentUpload';

interface Policy {
  id: string;
  policy_key: string;
  title: string;
  content: string;
  version: number;
  status: string;
  hipaa_references: string[];
  approved_by: string | null;
  approved_at: string | null;
  effective_date: string | null;
  review_due: string | null;
  created_at: string;
}

const STATUS_BADGE: Record<string, string> = {
  draft: 'bg-yellow-100 text-yellow-700',
  active: 'bg-green-100 text-green-700',
  archived: 'bg-slate-100 text-slate-600',
};

interface PolicyLibraryProps {
  apiBase?: string;
}

export const PolicyLibrary: React.FC<PolicyLibraryProps> = ({ apiBase = '/api/client/compliance' }) => {
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [templates, setTemplates] = useState<string[]>([]);
  const [selected, setSelected] = useState<Policy | null>(null);
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => { fetchPolicies(); }, []);

  const fetchPolicies = async () => {
    const res = await fetch(`${apiBase}/policies`, { credentials: 'include' });
    if (res.ok) {
      const data = await res.json();
      setPolicies(data.policies || []);
      setTemplates(data.available_templates || []);
    }
    setLoading(false);
  };

  const createFromTemplate = async (key: string) => {
    const res = await fetch(`${apiBase}/policies`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ policy_key: key }),
    });
    if (res.ok) {
      await fetchPolicies();
    }
  };

  const savePolicy = async () => {
    if (!selected) return;
    await fetch(`${apiBase}/policies/${selected.id}`, {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: editContent }),
    });
    setEditing(false);
    await fetchPolicies();
  };

  const approvePolicy = async (id: string) => {
    await fetch(`${apiBase}/policies/${id}/approve`, {
      method: 'POST',
      credentials: 'include',
    });
    await fetchPolicies();
    setSelected(null);
  };

  const usedKeys = new Set(policies.map(p => p.policy_key));
  const availableTemplates = templates.filter(t => !usedKeys.has(t));

  if (loading) return <div className="text-center py-12 text-slate-500">Loading policies...</div>;

  // Detail view
  if (selected) {
    return (
      <div>
        <button onClick={() => { setSelected(null); setEditing(false); }} className="mb-4 text-sm text-slate-500 hover:text-teal-600">
          &larr; Back to policies
        </button>
        <div className="bg-white rounded-2xl border border-slate-100 p-8">
          <div className="flex items-center justify-between mb-6">
            <div>
              <h2 className="text-xl font-bold text-slate-900">{selected.title}</h2>
              <div className="flex items-center gap-2 mt-1">
                <span className={`px-2 py-0.5 text-xs rounded-full ${STATUS_BADGE[selected.status] || STATUS_BADGE.draft}`}>{selected.status}</span>
                <span className="text-xs text-slate-400">v{selected.version}</span>
                {selected.hipaa_references?.map(r => (
                  <span key={r} className="px-2 py-0.5 text-xs rounded-full bg-blue-50 text-blue-600">{r}</span>
                ))}
              </div>
            </div>
            <div className="flex gap-2">
              {selected.status === 'draft' && (
                <>
                  <button onClick={() => { setEditing(true); setEditContent(selected.content); }} className="px-3 py-1.5 text-sm border border-slate-200 rounded-lg hover:bg-slate-50">
                    Edit
                  </button>
                  <button onClick={() => approvePolicy(selected.id)} className="px-3 py-1.5 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700">
                    Approve & Activate
                  </button>
                </>
              )}
            </div>
          </div>

          {editing ? (
            <div>
              <textarea
                className="w-full border border-slate-200 rounded-lg p-4 text-sm font-mono"
                rows={20}
                value={editContent}
                onChange={e => setEditContent(e.target.value)}
              />
              <div className="flex gap-2 mt-4">
                <button onClick={savePolicy} className="px-4 py-2 text-sm bg-teal-600 text-white rounded-lg hover:bg-teal-700">Save</button>
                <button onClick={() => setEditing(false)} className="px-4 py-2 text-sm text-slate-500 hover:bg-slate-100 rounded-lg">Cancel</button>
              </div>
            </div>
          ) : (
            <div className="prose prose-sm max-w-none">
              <pre className="whitespace-pre-wrap text-sm text-slate-700 bg-slate-50 p-6 rounded-xl">{selected.content}</pre>
            </div>
          )}

          {selected.approved_by && (
            <div className="mt-6 p-4 bg-green-50 rounded-xl text-sm">
              <p className="text-green-700">Approved by {selected.approved_by} on {selected.approved_at ? new Date(selected.approved_at).toLocaleDateString() : 'N/A'}</p>
              {selected.review_due && <p className="text-green-600 mt-1">Review due: {new Date(selected.review_due).toLocaleDateString()}</p>}
            </div>
          )}
        </div>
      </div>
    );
  }

  // List view
  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-bold text-slate-900">Policy Library</h2>
      </div>

      {/* Existing policies */}
      {policies.length > 0 && (
        <div className="space-y-3 mb-8">
          {policies.map(p => (
            <button
              key={p.id}
              onClick={() => setSelected(p)}
              className="w-full bg-white rounded-2xl border border-slate-100 p-5 flex items-center justify-between hover:border-teal-300 transition-all text-left"
            >
              <div>
                <h3 className="font-semibold text-slate-900">{p.title}</h3>
                <div className="flex items-center gap-2 mt-1">
                  <span className="text-xs text-slate-400">v{p.version}</span>
                  {p.review_due && new Date(p.review_due) < new Date() && (
                    <span className="text-xs text-red-500">Review overdue</span>
                  )}
                </div>
              </div>
              <span className={`px-2 py-0.5 text-xs rounded-full ${STATUS_BADGE[p.status] || STATUS_BADGE.draft}`}>{p.status}</span>
            </button>
          ))}
        </div>
      )}

      {/* Available templates */}
      {availableTemplates.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Create from Template</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {availableTemplates.map(key => (
              <button
                key={key}
                onClick={() => createFromTemplate(key)}
                className="bg-white rounded-xl border border-dashed border-slate-300 p-4 text-left hover:border-teal-400 hover:bg-teal-50/30 transition-all"
              >
                <p className="text-sm font-medium text-slate-700">{key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}</p>
                <p className="text-xs text-slate-400 mt-1">Click to generate policy from template</p>
              </button>
            ))}
          </div>
        </div>
      )}

      {policies.length === 0 && availableTemplates.length === 0 && (
        <div className="bg-white rounded-2xl border border-slate-100 p-12 text-center">
          <p className="text-slate-500">No policies available.</p>
        </div>
      )}

      <DocumentUpload moduleKey="policies" apiBase={apiBase} />
    </div>
  );
};
