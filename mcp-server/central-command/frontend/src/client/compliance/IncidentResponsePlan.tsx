import React, { useState, useEffect } from 'react';
import { DocumentUpload } from './DocumentUpload';

interface IRPlan {
  id: string;
  title: string;
  content: string;
  version: number;
  status: string;
  last_tested: string | null;
  next_review: string | null;
  approved_by: string | null;
  approved_at: string | null;
}

interface Breach {
  id: string;
  incident_date: string;
  discovered_date: string;
  description: string;
  phi_involved: boolean;
  individuals_affected: number;
  breach_type: string | null;
  notification_required: boolean;
  hhs_notified: boolean;
  individuals_notified: boolean;
  root_cause: string | null;
  corrective_actions: string | null;
  status: string;
}

const BREACH_EMPTY: Partial<Breach> = {
  incident_date: new Date().toISOString().split('T')[0],
  discovered_date: new Date().toISOString().split('T')[0],
  description: '', phi_involved: false, individuals_affected: 0,
  breach_type: '', notification_required: false,
  root_cause: '', corrective_actions: '', status: 'investigating',
};

const STATUS_BADGE: Record<string, string> = {
  investigating: 'bg-yellow-100 text-yellow-700',
  contained: 'bg-blue-100 text-blue-700',
  resolved: 'bg-green-100 text-green-700',
  closed: 'bg-slate-100 text-slate-600',
};

interface IncidentResponsePlanProps {
  apiBase?: string;
}

export const IncidentResponsePlan: React.FC<IncidentResponsePlanProps> = ({ apiBase = '/api/client/compliance' }) => {
  const [plan, setPlan] = useState<IRPlan | null>(null);
  const [breaches, setBreaches] = useState<Breach[]>([]);
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState('');
  const [showBreachForm, setShowBreachForm] = useState(false);
  const [breachForm, setBreachForm] = useState<Partial<Breach>>(BREACH_EMPTY);
  const [editBreachId, setEditBreachId] = useState<string | null>(null);

  useEffect(() => { fetchData(); }, []);

  const fetchData = async () => {
    const res = await fetch(`${apiBase}/ir-plan`, { credentials: 'include' });
    if (res.ok) {
      const d = await res.json();
      setPlan(d.plan);
      setBreaches(d.breaches || []);
    }
  };

  const savePlan = async () => {
    await fetch(`${apiBase}/ir-plan`, {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'Incident Response Plan', content: editContent }),
    });
    setEditing(false);
    await fetchData();
  };

  const saveBreach = async () => {
    const method = editBreachId ? 'PUT' : 'POST';
    const url = editBreachId ? `${apiBase}/breaches/${editBreachId}` : `${apiBase}/breaches`;
    await fetch(url, {
      method, credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(breachForm),
    });
    setShowBreachForm(false); setEditBreachId(null); setBreachForm(BREACH_EMPTY);
    await fetchData();
  };

  return (
    <div>
      <h2 className="text-xl font-bold text-slate-900 mb-4">Incident Response Plan</h2>
      <div className="mb-6 px-6 py-5 bg-teal-50/60 rounded-2xl border border-teal-100">
        <p className="text-sm font-medium text-teal-900 mb-1">What is this?</p>
        <p className="text-sm text-teal-800 leading-relaxed">Your Incident Response Plan documents what your practice will do if patient data is lost, stolen, or accessed by an unauthorized person. HIPAA requires you to have a written plan before a breach happens — not after. The Breach Log below tracks any incidents if they occur.</p>
        <p className="text-sm font-medium text-teal-900 mt-3 mb-1">How to complete it</p>
        <p className="text-sm text-teal-800 leading-relaxed">Upload your existing IR plan in Supporting Documents below. If you don't have one, download our template, customize it for your practice (contacts, backup systems, escalation procedures), and re-upload. HIPAA requires periodic review — use "Mark as Reviewed" to track your annual review cycle.</p>
      </div>

      {/* IR Plan Status Panel */}
      <div className="bg-white rounded-2xl border border-slate-100 p-6 mb-6">
        {plan ? (
          <div>
            <div className="flex items-center gap-3 mb-4">
              <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${plan.status === 'active' ? 'bg-green-100' : 'bg-yellow-100'}`}>
                <svg className={`w-5 h-5 ${plan.status === 'active' ? 'text-green-600' : 'text-yellow-600'}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={plan.status === 'active' ? 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z' : 'M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z'} />
                </svg>
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <h3 className="font-semibold text-slate-900">IR Plan Status:</h3>
                  <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${plan.status === 'active' ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'}`}>
                    {plan.status === 'active' ? 'Active' : plan.status}
                  </span>
                </div>
                <p className="text-xs text-slate-400 mt-0.5">Version {plan.version}</p>
              </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4 p-4 bg-slate-50 rounded-xl">
              <div>
                <p className="text-xs text-slate-400">Document</p>
                <p className="text-sm font-medium text-slate-900 mt-0.5">{plan.title}</p>
              </div>
              <div>
                <p className="text-xs text-slate-400">Last Updated</p>
                <p className="text-sm font-medium text-slate-900 mt-0.5">{plan.approved_at ? new Date(plan.approved_at).toLocaleDateString() : 'Not yet'}</p>
              </div>
              <div>
                <p className="text-xs text-slate-400">Last Reviewed</p>
                <p className="text-sm font-medium text-slate-900 mt-0.5">{plan.last_tested ? new Date(plan.last_tested).toLocaleDateString() : 'Not yet reviewed'}</p>
              </div>
              <div>
                <p className="text-xs text-slate-400">Next Review Due</p>
                <p className="text-sm font-medium text-slate-900 mt-0.5">{plan.next_review ? new Date(plan.next_review).toLocaleDateString() : 'Not set'}</p>
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              <button onClick={async () => {
                await fetch(`${apiBase}/ir-plan/review`, { method: 'POST', credentials: 'include' });
                fetchData();
              }} className="px-3 py-1.5 text-sm border border-slate-200 rounded-lg hover:bg-slate-50 text-slate-700">
                Mark as Reviewed
              </button>
              <button onClick={() => { setEditing(true); setEditContent(plan.content); }} className="px-3 py-1.5 text-sm border border-slate-200 rounded-lg hover:bg-slate-50 text-slate-700">
                Edit Plan
              </button>
            </div>

            {editing && (
              <div className="mt-4">
                <textarea className="w-full border border-slate-200 rounded-lg p-4 text-sm font-mono" rows={15} value={editContent} onChange={e => setEditContent(e.target.value)} />
                <div className="flex gap-2 mt-3">
                  <button onClick={savePlan} className="px-4 py-2 text-sm bg-teal-600 text-white rounded-lg hover:bg-teal-700">Save New Version</button>
                  <button onClick={() => setEditing(false)} className="px-4 py-2 text-sm text-slate-500 hover:bg-slate-100 rounded-lg">Cancel</button>
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="p-4 bg-amber-50 border border-amber-200 rounded-xl">
            <div className="flex items-start gap-3">
              <svg className="w-5 h-5 text-amber-500 mt-0.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
              </svg>
              <div>
                <p className="text-sm font-medium text-amber-900">No Incident Response Plan on file</p>
                <p className="text-sm text-amber-700 mt-1">HIPAA requires a written IR plan before a breach occurs. Upload your existing plan in Supporting Documents below, or download our template, customize it, and re-upload.</p>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Breach Log */}
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold text-slate-900">Breach Log</h3>
        <button onClick={() => { setShowBreachForm(true); setBreachForm(BREACH_EMPTY); setEditBreachId(null); }} className="px-3 py-1.5 text-sm bg-red-600 text-white rounded-lg hover:bg-red-700">
          Log Breach
        </button>
      </div>

      {showBreachForm && (
        <div className="bg-white rounded-2xl border border-slate-100 p-6 mb-6">
          <h3 className="font-semibold text-slate-900 mb-4">{editBreachId ? 'Edit' : 'Log'} Breach</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Incident Date *</label>
              <input type="date" className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={breachForm.incident_date || ''} onChange={e => setBreachForm({ ...breachForm, incident_date: e.target.value })} />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Discovered Date *</label>
              <input type="date" className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={breachForm.discovered_date || ''} onChange={e => setBreachForm({ ...breachForm, discovered_date: e.target.value })} />
            </div>
            <div className="col-span-2">
              <label className="block text-xs text-slate-500 mb-1">Description *</label>
              <textarea className="w-full border border-slate-200 rounded-lg p-2 text-sm" rows={3} value={breachForm.description || ''} onChange={e => setBreachForm({ ...breachForm, description: e.target.value })} />
            </div>
            <div>
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input type="checkbox" checked={breachForm.phi_involved || false} onChange={e => setBreachForm({ ...breachForm, phi_involved: e.target.checked })} />
                PHI Involved
              </label>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Individuals Affected</label>
              <input type="number" className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={breachForm.individuals_affected || 0} onChange={e => setBreachForm({ ...breachForm, individuals_affected: parseInt(e.target.value) || 0 })} />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Breach Type</label>
              <select className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={breachForm.breach_type || ''} onChange={e => setBreachForm({ ...breachForm, breach_type: e.target.value })}>
                <option value="">Select...</option>
                <option value="unauthorized_access">Unauthorized Access</option>
                <option value="theft">Theft</option>
                <option value="loss">Loss</option>
                <option value="hacking">Hacking</option>
                <option value="improper_disposal">Improper Disposal</option>
                <option value="other">Other</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Status</label>
              <select className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={breachForm.status || 'investigating'} onChange={e => setBreachForm({ ...breachForm, status: e.target.value })}>
                <option value="investigating">Investigating</option>
                <option value="contained">Contained</option>
                <option value="resolved">Resolved</option>
                <option value="closed">Closed</option>
              </select>
            </div>
            <div className="col-span-2">
              <label className="block text-xs text-slate-500 mb-1">Root Cause</label>
              <textarea className="w-full border border-slate-200 rounded-lg p-2 text-sm" rows={2} value={breachForm.root_cause || ''} onChange={e => setBreachForm({ ...breachForm, root_cause: e.target.value })} />
            </div>
            <div className="col-span-2">
              <label className="block text-xs text-slate-500 mb-1">Corrective Actions</label>
              <textarea className="w-full border border-slate-200 rounded-lg p-2 text-sm" rows={2} value={breachForm.corrective_actions || ''} onChange={e => setBreachForm({ ...breachForm, corrective_actions: e.target.value })} />
            </div>
          </div>
          <div className="flex gap-2 mt-4">
            <button onClick={saveBreach} className="px-4 py-2 text-sm bg-teal-600 text-white rounded-lg hover:bg-teal-700">Save</button>
            <button onClick={() => { setShowBreachForm(false); setEditBreachId(null); }} className="px-4 py-2 text-sm text-slate-500 hover:bg-slate-100 rounded-lg">Cancel</button>
          </div>
        </div>
      )}

      {breaches.length === 0 ? (
        <div className="bg-white rounded-2xl border border-slate-100 p-8 text-center">
          <p className="text-slate-500 text-sm">No breaches logged. This is good.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {breaches.map(b => (
            <div key={b.id} className="bg-white rounded-2xl border border-slate-100 p-5">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-slate-900">{b.description.substring(0, 100)}{b.description.length > 100 ? '...' : ''}</p>
                  <p className="text-xs text-slate-500 mt-1">
                    Incident: {new Date(b.incident_date).toLocaleDateString()}
                    {b.phi_involved && ' | PHI involved'}
                    {b.individuals_affected > 0 && ` | ${b.individuals_affected} affected`}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <span className={`px-2 py-0.5 text-xs rounded-full ${STATUS_BADGE[b.status] || STATUS_BADGE.investigating}`}>{b.status}</span>
                  <button onClick={() => { setBreachForm(b); setEditBreachId(b.id); setShowBreachForm(true); }} className="text-xs text-teal-600">Edit</button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <DocumentUpload moduleKey="ir_plan" apiBase={apiBase} />
    </div>
  );
};
