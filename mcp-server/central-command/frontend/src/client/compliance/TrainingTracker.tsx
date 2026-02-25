import React, { useState, useEffect } from 'react';
import { DocumentUpload } from './DocumentUpload';

interface TrainingRecord {
  id: string;
  employee_name: string;
  employee_email: string | null;
  employee_role: string | null;
  training_type: string;
  training_topic: string;
  completed_date: string | null;
  due_date: string;
  status: string;
  certificate_ref: string | null;
  trainer: string | null;
  notes: string | null;
}

const EMPTY: TrainingRecord = {
  id: '', employee_name: '', employee_email: '', employee_role: '',
  training_type: 'initial', training_topic: '', completed_date: null,
  due_date: new Date().toISOString().split('T')[0], status: 'pending',
  certificate_ref: null, trainer: null, notes: null,
};

const STATUS_BADGE: Record<string, string> = {
  pending: 'bg-yellow-100 text-yellow-700',
  completed: 'bg-green-100 text-green-700',
  overdue: 'bg-red-100 text-red-700',
};

interface TrainingTrackerProps {
  apiBase?: string;
}

export const TrainingTracker: React.FC<TrainingTrackerProps> = ({ apiBase = '/api/client/compliance' }) => {
  const [records, setRecords] = useState<TrainingRecord[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<TrainingRecord>(EMPTY);
  const [editId, setEditId] = useState<string | null>(null);

  useEffect(() => { fetchRecords(); }, []);

  const fetchRecords = async () => {
    const res = await fetch(`${apiBase}/training`, { credentials: 'include' });
    if (res.ok) { const d = await res.json(); setRecords(d.records || []); }
  };

  const save = async () => {
    const method = editId ? 'PUT' : 'POST';
    const url = editId ? `${apiBase}/training/${editId}` : `${apiBase}/training`;
    await fetch(url, {
      method, credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form),
    });
    setShowForm(false); setEditId(null); setForm(EMPTY);
    await fetchRecords();
  };

  const remove = async (id: string) => {
    await fetch(`${apiBase}/training/${id}`, { method: 'DELETE', credentials: 'include' });
    await fetchRecords();
  };

  const edit = (r: TrainingRecord) => {
    setForm(r); setEditId(r.id); setShowForm(true);
  };

  const getDisplayStatus = (r: TrainingRecord) => {
    if (r.status === 'completed') return 'completed';
    if (new Date(r.due_date) < new Date()) return 'overdue';
    return 'pending';
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold text-slate-900">Training Records</h2>
        <button onClick={() => { setShowForm(true); setForm(EMPTY); setEditId(null); }} className="px-4 py-2 bg-teal-600 text-white rounded-lg hover:bg-teal-700 text-sm">
          Add Record
        </button>
      </div>
      <div className="mb-6 p-4 bg-teal-50/60 rounded-xl border border-teal-100">
        <p className="text-sm font-medium text-teal-900 mb-1">What is this?</p>
        <p className="text-sm text-teal-800">HIPAA requires every workforce member who handles patient information to complete security awareness training. New hires need initial training, and everyone needs an annual refresher. This section tracks who has been trained and when they're due for renewal.</p>
        <p className="text-sm font-medium text-teal-900 mt-3 mb-1">How to complete it</p>
        <p className="text-sm text-teal-800">Click "Add Record" for each employee. Enter their name, the training topic (e.g., HIPAA Privacy & Security Basics), the type (initial for new hires, annual for renewals), and the due date. Mark it completed once they finish. Upload completion certificates or sign-in sheets in Supporting Documents below.</p>
      </div>

      {showForm && (
        <div className="bg-white rounded-2xl border border-slate-100 p-6 mb-6">
          <h3 className="font-semibold text-slate-900 mb-4">{editId ? 'Edit' : 'New'} Training Record</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Employee Name *</label>
              <input className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.employee_name} onChange={e => setForm({ ...form, employee_name: e.target.value })} />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Email</label>
              <input className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.employee_email || ''} onChange={e => setForm({ ...form, employee_email: e.target.value })} />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Role</label>
              <select className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.employee_role || ''} onChange={e => setForm({ ...form, employee_role: e.target.value })}>
                <option value="">Select...</option>
                <option value="clinical">Clinical</option>
                <option value="admin">Administrative</option>
                <option value="IT">IT</option>
                <option value="management">Management</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Training Type *</label>
              <select className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.training_type} onChange={e => setForm({ ...form, training_type: e.target.value })}>
                <option value="initial">Initial</option>
                <option value="annual">Annual</option>
                <option value="remedial">Remedial</option>
                <option value="specialized">Specialized</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Topic *</label>
              <input className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.training_topic} onChange={e => setForm({ ...form, training_topic: e.target.value })} placeholder="e.g. HIPAA Privacy, Security Awareness" />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Due Date *</label>
              <input type="date" className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.due_date} onChange={e => setForm({ ...form, due_date: e.target.value })} />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Completed Date</label>
              <input type="date" className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.completed_date || ''} onChange={e => setForm({ ...form, completed_date: e.target.value || null, status: e.target.value ? 'completed' : 'pending' })} />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Trainer</label>
              <input className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.trainer || ''} onChange={e => setForm({ ...form, trainer: e.target.value })} />
            </div>
          </div>
          <div className="flex gap-2 mt-4">
            <button onClick={save} className="px-4 py-2 text-sm bg-teal-600 text-white rounded-lg hover:bg-teal-700">Save</button>
            <button onClick={() => { setShowForm(false); setEditId(null); }} className="px-4 py-2 text-sm text-slate-500 hover:bg-slate-100 rounded-lg">Cancel</button>
          </div>
        </div>
      )}

      {records.length === 0 ? (
        <div className="bg-white rounded-2xl border border-slate-100 p-12 text-center">
          <p className="text-slate-500">No training records yet. Add your first record to start tracking.</p>
        </div>
      ) : (
        <div className="bg-white rounded-2xl border border-slate-100 overflow-hidden">
          <table className="w-full">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">Employee</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">Topic</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">Type</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">Due Date</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">Status</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-slate-500">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {records.map(r => {
                const displayStatus = getDisplayStatus(r);
                return (
                  <tr key={r.id} className="hover:bg-teal-50/50">
                    <td className="px-4 py-3">
                      <p className="text-sm font-medium text-slate-900">{r.employee_name}</p>
                      <p className="text-xs text-slate-400">{r.employee_role || ''}</p>
                    </td>
                    <td className="px-4 py-3 text-sm text-slate-700">{r.training_topic}</td>
                    <td className="px-4 py-3 text-sm text-slate-500 capitalize">{r.training_type}</td>
                    <td className="px-4 py-3 text-sm text-slate-500">{new Date(r.due_date).toLocaleDateString()}</td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 text-xs rounded-full ${STATUS_BADGE[displayStatus]}`}>{displayStatus}</span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button onClick={() => edit(r)} className="text-xs text-teal-600 hover:text-teal-700 mr-2">Edit</button>
                      <button onClick={() => remove(r.id)} className="text-xs text-red-500 hover:text-red-600">Remove</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <DocumentUpload moduleKey="training" apiBase={apiBase} />
    </div>
  );
};
