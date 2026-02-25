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

interface TemplateInfo {
  key: string;
  title: string;
  hipaa_references: string[];
  preview: string;
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
  const [templateInfo, setTemplateInfo] = useState<TemplateInfo[]>([]);
  const [selected, setSelected] = useState<Policy | null>(null);
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState('');
  const [loading, setLoading] = useState(true);
  const [expandedTemplates, setExpandedTemplates] = useState<Set<string>>(new Set());
  const [templateContents, setTemplateContents] = useState<Record<string, string>>({});

  useEffect(() => { fetchPolicies(); fetchTemplates(); }, []);

  const fetchPolicies = async () => {
    const res = await fetch(`${apiBase}/policies`, { credentials: 'include' });
    if (res.ok) {
      const data = await res.json();
      setPolicies(data.policies || []);
      setTemplates(data.available_templates || []);
    }
    setLoading(false);
  };

  const fetchTemplates = async () => {
    const res = await fetch(`${apiBase}/policies/templates`, { credentials: 'include' });
    if (res.ok) {
      const data = await res.json();
      setTemplateInfo(data.templates || []);
    }
  };

  const toggleExpand = async (key: string) => {
    const next = new Set(expandedTemplates);
    if (next.has(key)) {
      next.delete(key);
    } else {
      next.add(key);
      if (!templateContents[key]) {
        const res = await fetch(`${apiBase}/policies/templates/${key}`, { credentials: 'include' });
        if (res.ok) {
          const data = await res.json();
          setTemplateContents(prev => ({ ...prev, [key]: data.content }));
        }
      }
    }
    setExpandedTemplates(next);
  };

  const downloadTemplate = async (key: string) => {
    const res = await fetch(`${apiBase}/policies/templates/${key}`, { credentials: 'include' });
    if (res.ok) {
      const data = await res.json();
      const blob = new Blob([data.content], { type: 'text/markdown' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${data.title.replace(/\s+/g, '_')}.md`;
      a.click();
      URL.revokeObjectURL(url);
    }
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
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold text-slate-900">Policy Library</h2>
      </div>
      <div className="mb-6 p-4 bg-teal-50/60 rounded-xl border border-teal-100">
        <p className="text-sm font-medium text-teal-900 mb-1">What is this?</p>
        <p className="text-sm text-teal-800">Your written HIPAA policies document how your practice protects patient information. Auditors will ask to see these. They cover topics like who can access records, how data is encrypted, and what happens when someone leaves the organization.</p>
        <p className="text-sm font-medium text-teal-900 mt-3 mb-1">How to complete it</p>
        <p className="text-sm text-teal-800">Use the templates below as a starting point — they're pre-filled with your organization's name and officers. Click "View" to read the full text, "Adopt" to bring it into your library, then edit any language to match your actual procedures. Once it accurately reflects your practice, click "Approve & Activate." If you already have written policies, upload them in the Supporting Documents section at the bottom.</p>
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
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Available Templates</h3>
          <p className="text-xs text-slate-400 mb-4">HIPAA-compliant policy templates pre-filled with your organization details. Preview the full text, download as a file, or adopt directly into your policy library.</p>
          <div className="space-y-3">
            {availableTemplates.map(key => {
              const info = templateInfo.find(t => t.key === key);
              const expanded = expandedTemplates.has(key);
              const fullContent = templateContents[key];
              return (
                <div
                  key={key}
                  className="bg-white rounded-2xl border border-slate-100 p-5"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <div className="w-8 h-8 rounded-lg bg-blue-50 flex items-center justify-center flex-shrink-0">
                          <svg className="w-4 h-4 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                          </svg>
                        </div>
                        <div>
                          <p className="text-sm font-semibold text-slate-900">{info?.title || key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}</p>
                          {info?.hipaa_references && (
                            <div className="flex gap-1 mt-0.5">
                              {info.hipaa_references.map(r => (
                                <span key={r} className="px-1.5 py-0.5 text-xs rounded bg-blue-50 text-blue-600">{r}</span>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>
                      {info?.preview && !expanded && (
                        <p className="text-xs text-slate-500 mt-2 line-clamp-2">{info.preview}</p>
                      )}
                    </div>
                    <div className="flex gap-2 flex-shrink-0">
                      <button
                        onClick={() => toggleExpand(key)}
                        className="px-3 py-1.5 text-xs border border-slate-200 text-slate-600 rounded-lg hover:bg-slate-50"
                      >
                        {expanded ? 'Collapse' : 'View'}
                      </button>
                      <button
                        onClick={() => downloadTemplate(key)}
                        className="px-3 py-1.5 text-xs border border-slate-200 text-slate-600 rounded-lg hover:bg-slate-50"
                      >
                        Download
                      </button>
                      <button
                        onClick={() => createFromTemplate(key)}
                        className="px-3 py-1.5 text-xs bg-teal-600 text-white rounded-lg hover:bg-teal-700"
                      >
                        Adopt
                      </button>
                    </div>
                  </div>
                  {expanded && fullContent && (
                    <div className="mt-4 border-t border-slate-100 pt-4">
                      <pre className="whitespace-pre-wrap text-xs text-slate-600 font-mono leading-relaxed bg-slate-50 p-4 rounded-xl max-h-96 overflow-y-auto">{fullContent}</pre>
                    </div>
                  )}
                </div>
              );
            })}
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
