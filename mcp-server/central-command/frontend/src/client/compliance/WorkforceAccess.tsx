import React, { useState, useEffect } from 'react';
import { DocumentUpload } from './DocumentUpload';

interface Member {
  id: string;
  employee_name: string;
  employee_role: string | null;
  department: string | null;
  access_level: string;
  systems: string[];
  start_date: string;
  termination_date: string | null;
  access_revoked_date: string | null;
  status: string;
  supervisor: string | null;
  notes: string | null;
}

const EMPTY: Member = {
  id: '', employee_name: '', employee_role: '', department: '',
  access_level: 'limited', systems: [], start_date: new Date().toISOString().split('T')[0],
  termination_date: null, access_revoked_date: null, status: 'active',
  supervisor: '', notes: '',
};

const SYSTEMS = ['EHR', 'Billing', 'Email', 'File Share', 'Practice Mgmt', 'Lab Systems', 'Imaging'];
const STATUS_BADGE: Record<string, string> = {
  active: 'bg-green-100 text-green-700',
  terminated: 'bg-red-100 text-red-700',
  suspended: 'bg-yellow-100 text-yellow-700',
};

interface WorkforceAccessProps {
  apiBase?: string;
}

export const WorkforceAccess: React.FC<WorkforceAccessProps> = ({ apiBase = '/api/client/compliance' }) => {
  const [members, setMembers] = useState<Member[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<Member>(EMPTY);
  const [editId, setEditId] = useState<string | null>(null);

  useEffect(() => { fetchMembers(); }, []);

  const fetchMembers = async () => {
    const res = await fetch(`${apiBase}/workforce`, { credentials: 'include' });
    if (res.ok) { const d = await res.json(); setMembers(d.workforce || []); }
  };

  const save = async () => {
    const method = editId ? 'PUT' : 'POST';
    const url = editId ? `${apiBase}/workforce/${editId}` : `${apiBase}/workforce`;
    await fetch(url, {
      method, credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form),
    });
    setShowForm(false); setEditId(null); setForm(EMPTY);
    await fetchMembers();
  };

  const toggleSystem = (sys: string) => {
    setForm(prev => ({
      ...prev,
      systems: prev.systems.includes(sys) ? prev.systems.filter(s => s !== sys) : [...prev.systems, sys],
    }));
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-bold text-slate-900">Workforce Access Management</h2>
        <button onClick={() => { setShowForm(true); setForm(EMPTY); setEditId(null); }} className="px-4 py-2 bg-teal-600 text-white rounded-lg hover:bg-teal-700 text-sm">
          Add Member
        </button>
      </div>

      {showForm && (
        <div className="bg-white rounded-2xl border border-slate-100 p-6 mb-6">
          <h3 className="font-semibold text-slate-900 mb-4">{editId ? 'Edit' : 'Add'} Workforce Member</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Name *</label>
              <input className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.employee_name} onChange={e => setForm({ ...form, employee_name: e.target.value })} />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Role</label>
              <input className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.employee_role || ''} onChange={e => setForm({ ...form, employee_role: e.target.value })} placeholder="e.g. Dental Hygienist" />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Department</label>
              <input className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.department || ''} onChange={e => setForm({ ...form, department: e.target.value })} />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Access Level *</label>
              <select className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.access_level} onChange={e => setForm({ ...form, access_level: e.target.value })}>
                <option value="full">Full</option>
                <option value="limited">Limited</option>
                <option value="read_only">Read Only</option>
                <option value="none">None</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Start Date *</label>
              <input type="date" className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.start_date} onChange={e => setForm({ ...form, start_date: e.target.value })} />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Supervisor</label>
              <input className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.supervisor || ''} onChange={e => setForm({ ...form, supervisor: e.target.value })} />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Status</label>
              <select className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.status} onChange={e => setForm({ ...form, status: e.target.value })}>
                <option value="active">Active</option>
                <option value="terminated">Terminated</option>
                <option value="suspended">Suspended</option>
              </select>
            </div>
            {(form.status === 'terminated') && (
              <>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Termination Date</label>
                  <input type="date" className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.termination_date || ''} onChange={e => setForm({ ...form, termination_date: e.target.value || null })} />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Access Revoked Date</label>
                  <input type="date" className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.access_revoked_date || ''} onChange={e => setForm({ ...form, access_revoked_date: e.target.value || null })} />
                </div>
              </>
            )}
            <div className="col-span-2">
              <label className="block text-xs text-slate-500 mb-1">Systems Access</label>
              <div className="flex flex-wrap gap-2">
                {SYSTEMS.map(sys => (
                  <button key={sys} onClick={() => toggleSystem(sys)}
                    className={`px-3 py-1 text-xs rounded-lg border ${form.systems.includes(sys) ? 'border-teal-500 bg-teal-50 text-teal-700' : 'border-slate-200 text-slate-500'}`}>
                    {sys}
                  </button>
                ))}
              </div>
            </div>
          </div>
          <div className="flex gap-2 mt-4">
            <button onClick={save} className="px-4 py-2 text-sm bg-teal-600 text-white rounded-lg hover:bg-teal-700">Save</button>
            <button onClick={() => { setShowForm(false); setEditId(null); }} className="px-4 py-2 text-sm text-slate-500 hover:bg-slate-100 rounded-lg">Cancel</button>
          </div>
        </div>
      )}

      {members.length === 0 ? (
        <div className="bg-white rounded-2xl border border-slate-100 p-12 text-center">
          <p className="text-slate-500">No workforce records yet. Track who has access to ePHI.</p>
        </div>
      ) : (
        <div className="bg-white rounded-2xl border border-slate-100 overflow-hidden">
          <table className="w-full">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">Employee</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">Access</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">Systems</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">Status</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-slate-500">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {members.map(m => (
                <tr key={m.id} className="hover:bg-teal-50/50">
                  <td className="px-4 py-3">
                    <p className="text-sm font-medium text-slate-900">{m.employee_name}</p>
                    <p className="text-xs text-slate-400">{m.employee_role}{m.department ? ` - ${m.department}` : ''}</p>
                  </td>
                  <td className="px-4 py-3 text-sm text-slate-600 capitalize">{m.access_level.replace('_', ' ')}</td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {m.systems.map(s => <span key={s} className="px-1.5 py-0.5 text-xs bg-slate-100 text-slate-600 rounded">{s}</span>)}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 text-xs rounded-full ${STATUS_BADGE[m.status]}`}>{m.status}</span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button onClick={() => { setForm(m); setEditId(m.id); setShowForm(true); }} className="text-xs text-teal-600 hover:text-teal-700">Edit</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <DocumentUpload moduleKey="workforce" apiBase={apiBase} />
    </div>
  );
};
