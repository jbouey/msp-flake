import React, { useState, useEffect } from 'react';

interface Plan {
  id: string;
  plan_type: string;
  title: string;
  content: string;
  rto_hours: number | null;
  rpo_hours: number | null;
  last_tested: string | null;
  next_test_due: string | null;
  test_result: string | null;
  status: string;
  approved_by: string | null;
}

const PLAN_TYPES = [
  { value: 'data_backup', label: 'Data Backup Plan' },
  { value: 'disaster_recovery', label: 'Disaster Recovery Plan' },
  { value: 'emergency_operations', label: 'Emergency Mode Operations Plan' },
];

const EMPTY: Partial<Plan> = {
  plan_type: 'data_backup', title: '', content: '',
  rto_hours: null, rpo_hours: null, status: 'draft',
};

export const ContingencyPlan: React.FC = () => {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<Partial<Plan>>(EMPTY);
  const [editId, setEditId] = useState<string | null>(null);
  const [viewPlan, setViewPlan] = useState<Plan | null>(null);

  useEffect(() => { fetchPlans(); }, []);

  const fetchPlans = async () => {
    const res = await fetch('/api/client/compliance/contingency', { credentials: 'include' });
    if (res.ok) { const d = await res.json(); setPlans(d.plans || []); }
  };

  const save = async () => {
    const method = editId ? 'PUT' : 'POST';
    const url = editId ? `/api/client/compliance/contingency/${editId}` : '/api/client/compliance/contingency';
    await fetch(url, {
      method, credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form),
    });
    setShowForm(false); setEditId(null); setForm(EMPTY);
    await fetchPlans();
  };

  if (viewPlan) {
    return (
      <div>
        <button onClick={() => setViewPlan(null)} className="mb-4 text-sm text-slate-500 hover:text-teal-600">&larr; Back</button>
        <div className="bg-white rounded-2xl border border-slate-100 p-8">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-xl font-bold text-slate-900">{viewPlan.title}</h2>
              <p className="text-xs text-slate-400 mt-1">
                {PLAN_TYPES.find(t => t.value === viewPlan.plan_type)?.label}
                {viewPlan.rto_hours && ` | RTO: ${viewPlan.rto_hours}h`}
                {viewPlan.rpo_hours && ` | RPO: ${viewPlan.rpo_hours}h`}
              </p>
            </div>
            <span className={`px-2 py-0.5 text-xs rounded-full ${viewPlan.status === 'active' ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'}`}>{viewPlan.status}</span>
          </div>
          <pre className="whitespace-pre-wrap text-sm text-slate-700 bg-slate-50 p-4 rounded-xl">{viewPlan.content}</pre>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-bold text-slate-900">Contingency Plans</h2>
        <button onClick={() => { setShowForm(true); setForm(EMPTY); setEditId(null); }} className="px-4 py-2 bg-teal-600 text-white rounded-lg hover:bg-teal-700 text-sm">
          Create Plan
        </button>
      </div>

      {showForm && (
        <div className="bg-white rounded-2xl border border-slate-100 p-6 mb-6">
          <h3 className="font-semibold text-slate-900 mb-4">{editId ? 'Edit' : 'New'} Contingency Plan</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Plan Type *</label>
              <select className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.plan_type} onChange={e => setForm({ ...form, plan_type: e.target.value })}>
                {PLAN_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Title *</label>
              <input className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.title || ''} onChange={e => setForm({ ...form, title: e.target.value })} />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">RTO (hours)</label>
              <input type="number" className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.rto_hours || ''} onChange={e => setForm({ ...form, rto_hours: parseInt(e.target.value) || null })} />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">RPO (hours)</label>
              <input type="number" className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.rpo_hours || ''} onChange={e => setForm({ ...form, rpo_hours: parseInt(e.target.value) || null })} />
            </div>
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Plan Content *</label>
            <textarea className="w-full border border-slate-200 rounded-lg p-4 text-sm font-mono" rows={12} value={form.content || ''} onChange={e => setForm({ ...form, content: e.target.value })} placeholder="Document your contingency plan..." />
          </div>
          <div className="flex gap-2 mt-4">
            <button onClick={save} className="px-4 py-2 text-sm bg-teal-600 text-white rounded-lg hover:bg-teal-700">Save</button>
            <button onClick={() => { setShowForm(false); setEditId(null); }} className="px-4 py-2 text-sm text-slate-500 hover:bg-slate-100 rounded-lg">Cancel</button>
          </div>
        </div>
      )}

      {plans.length === 0 ? (
        <div className="bg-white rounded-2xl border border-slate-100 p-12 text-center">
          <p className="text-slate-500">No contingency plans yet. HIPAA requires data backup, disaster recovery, and emergency operations plans.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {plans.map(p => (
            <button key={p.id} onClick={() => setViewPlan(p)} className="w-full bg-white rounded-2xl border border-slate-100 p-5 flex items-center justify-between hover:border-teal-300 transition-all text-left">
              <div>
                <h3 className="font-semibold text-slate-900">{p.title}</h3>
                <p className="text-xs text-slate-500 mt-1">
                  {PLAN_TYPES.find(t => t.value === p.plan_type)?.label}
                  {p.rto_hours && ` | RTO: ${p.rto_hours}h`}
                  {p.rpo_hours && ` | RPO: ${p.rpo_hours}h`}
                  {p.last_tested && ` | Tested: ${new Date(p.last_tested).toLocaleDateString()}`}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <span className={`px-2 py-0.5 text-xs rounded-full ${p.status === 'active' ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'}`}>{p.status}</span>
                <button onClick={e => { e.stopPropagation(); setForm(p); setEditId(p.id); setShowForm(true); }} className="text-xs text-teal-600">Edit</button>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
};
