import React, { useState, useEffect } from 'react';
import { DocumentUpload } from './DocumentUpload';

interface BAA {
  id: string;
  associate_name: string;
  associate_type: string;
  contact_name: string | null;
  contact_email: string | null;
  signed_date: string | null;
  expiry_date: string | null;
  auto_renew: boolean;
  status: string;
  phi_types: string[];
  services_description: string | null;
  notes: string | null;
}

const EMPTY: BAA = {
  id: '', associate_name: '', associate_type: 'it_support', contact_name: '',
  contact_email: '', signed_date: null, expiry_date: null, auto_renew: false,
  status: 'pending', phi_types: [], services_description: '', notes: '',
};

const STATUS_BADGE: Record<string, string> = {
  pending: 'bg-yellow-100 text-yellow-700',
  active: 'bg-green-100 text-green-700',
  expired: 'bg-red-100 text-red-700',
  terminated: 'bg-slate-100 text-slate-600',
};

const PHI_OPTIONS = ['demographics', 'clinical', 'billing', 'insurance', 'mental_health', 'substance_abuse'];
const TYPE_OPTIONS = [
  { value: 'cloud_provider', label: 'Cloud Provider' },
  { value: 'billing', label: 'Billing Service' },
  { value: 'it_support', label: 'IT Support' },
  { value: 'shredding', label: 'Shredding Service' },
  { value: 'ehr_vendor', label: 'EHR Vendor' },
  { value: 'other', label: 'Other' },
];

interface BAATrackerProps {
  apiBase?: string;
}

export const BAATracker: React.FC<BAATrackerProps> = ({ apiBase = '/api/client/compliance' }) => {
  const [baas, setBaas] = useState<BAA[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<BAA>(EMPTY);
  const [editId, setEditId] = useState<string | null>(null);

  useEffect(() => { fetchBaas(); }, []);

  const fetchBaas = async () => {
    const res = await fetch(`${apiBase}/baas`, { credentials: 'include' });
    if (res.ok) { const d = await res.json(); setBaas(d.baas || []); }
  };

  const save = async () => {
    const method = editId ? 'PUT' : 'POST';
    const url = editId ? `${apiBase}/baas/${editId}` : `${apiBase}/baas`;
    await fetch(url, {
      method, credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form),
    });
    setShowForm(false); setEditId(null); setForm(EMPTY);
    await fetchBaas();
  };

  const remove = async (id: string) => {
    await fetch(`${apiBase}/baas/${id}`, { method: 'DELETE', credentials: 'include' });
    await fetchBaas();
  };

  const togglePhi = (phi: string) => {
    setForm(prev => ({
      ...prev,
      phi_types: prev.phi_types.includes(phi)
        ? prev.phi_types.filter(p => p !== phi)
        : [...prev.phi_types, phi],
    }));
  };

  const isExpiringSoon = (b: BAA) =>
    b.status === 'active' && b.expiry_date &&
    new Date(b.expiry_date) < new Date(Date.now() + 90 * 24 * 60 * 60 * 1000);

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold text-slate-900">Business Associate Agreements</h2>
        <button onClick={() => { setShowForm(true); setForm(EMPTY); setEditId(null); }} className="px-4 py-2 bg-teal-600 text-white rounded-lg hover:bg-teal-700 text-sm">
          Add BAA
        </button>
      </div>
      <div className="mb-6 px-6 py-5 bg-teal-50/60 rounded-2xl border border-teal-100">
        <p className="text-sm font-medium text-teal-900 mb-1">What is this?</p>
        <p className="text-sm text-teal-800 leading-relaxed">A Business Associate Agreement (BAA) is a written contract required any time a third-party vendor can access, store, or transmit patient data on your behalf. Common examples: your IT company, EHR vendor, cloud backup provider, billing service, and shredding company.</p>
        <p className="text-sm font-medium text-teal-900 mt-3 mb-1">How to complete it</p>
        <p className="text-sm text-teal-800 leading-relaxed">Click "Add BAA" for each vendor that touches patient data. Enter the vendor name, type, signed date, and which types of data they can access. Upload the signed BAA PDF in Supporting Documents below. Review expiration dates — most should be renewed annually or match your contract terms.</p>
      </div>

      {showForm && (
        <div className="bg-white rounded-2xl border border-slate-100 p-6 mb-6">
          <h3 className="font-semibold text-slate-900 mb-4">{editId ? 'Edit' : 'New'} BAA</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Associate Name *</label>
              <input className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.associate_name} onChange={e => setForm({ ...form, associate_name: e.target.value })} />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Type *</label>
              <select className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.associate_type} onChange={e => setForm({ ...form, associate_type: e.target.value })}>
                {TYPE_OPTIONS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Contact Name</label>
              <input className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.contact_name || ''} onChange={e => setForm({ ...form, contact_name: e.target.value })} />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Contact Email</label>
              <input className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.contact_email || ''} onChange={e => setForm({ ...form, contact_email: e.target.value })} />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Signed Date</label>
              <input type="date" className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.signed_date || ''} onChange={e => setForm({ ...form, signed_date: e.target.value || null, status: e.target.value ? 'active' : form.status })} />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Expiry Date</label>
              <input type="date" className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={form.expiry_date || ''} onChange={e => setForm({ ...form, expiry_date: e.target.value || null })} />
            </div>
            <div className="col-span-2">
              <label className="block text-xs text-slate-500 mb-1">PHI Types Accessed</label>
              <div className="flex flex-wrap gap-2">
                {PHI_OPTIONS.map(phi => (
                  <button key={phi} onClick={() => togglePhi(phi)}
                    className={`px-3 py-1 text-xs rounded-lg border ${form.phi_types.includes(phi) ? 'border-teal-500 bg-teal-50 text-teal-700' : 'border-slate-200 text-slate-500'}`}>
                    {phi.replace(/_/g, ' ')}
                  </button>
                ))}
              </div>
            </div>
            <div className="col-span-2">
              <label className="block text-xs text-slate-500 mb-1">Services Description</label>
              <textarea className="w-full border border-slate-200 rounded-lg p-2 text-sm" rows={2} value={form.services_description || ''} onChange={e => setForm({ ...form, services_description: e.target.value })} />
            </div>
          </div>
          <div className="flex gap-2 mt-4">
            <button onClick={save} className="px-4 py-2 text-sm bg-teal-600 text-white rounded-lg hover:bg-teal-700">Save</button>
            <button onClick={() => { setShowForm(false); setEditId(null); }} className="px-4 py-2 text-sm text-slate-500 hover:bg-slate-100 rounded-lg">Cancel</button>
          </div>
        </div>
      )}

      {baas.length === 0 ? (
        <div className="bg-white rounded-2xl border border-slate-100 p-12 text-center">
          <p className="text-slate-500">No BAAs tracked yet. Add your business associate agreements.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {baas.map(b => (
            <div key={b.id} className="bg-white rounded-2xl border border-slate-100 p-5 flex items-center justify-between">
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="font-semibold text-slate-900">{b.associate_name}</h3>
                  <span className={`px-2 py-0.5 text-xs rounded-full ${STATUS_BADGE[b.status]}`}>{b.status}</span>
                  {isExpiringSoon(b) && <span className="px-2 py-0.5 text-xs rounded-full bg-orange-100 text-orange-700">Expiring soon</span>}
                </div>
                <p className="text-xs text-slate-500 mt-1">
                  {TYPE_OPTIONS.find(t => t.value === b.associate_type)?.label || b.associate_type}
                  {b.signed_date && ` | Signed ${new Date(b.signed_date).toLocaleDateString()}`}
                  {b.expiry_date && ` | Expires ${new Date(b.expiry_date).toLocaleDateString()}`}
                </p>
                {b.phi_types.length > 0 && (
                  <div className="flex gap-1 mt-2">
                    {b.phi_types.map(p => <span key={p} className="px-1.5 py-0.5 text-xs bg-blue-50 text-blue-600 rounded">{p}</span>)}
                  </div>
                )}
              </div>
              <div className="flex gap-2">
                <button onClick={() => { setForm(b); setEditId(b.id); setShowForm(true); }} className="text-xs text-teal-600 hover:text-teal-700">Edit</button>
                <button onClick={() => remove(b.id)} className="text-xs text-red-500 hover:text-red-600">Remove</button>
              </div>
            </div>
          ))}
        </div>
      )}

      <DocumentUpload moduleKey="baas" apiBase={apiBase} />
    </div>
  );
};
