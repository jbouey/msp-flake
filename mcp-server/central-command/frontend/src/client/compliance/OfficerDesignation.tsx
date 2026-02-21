import React, { useState, useEffect } from 'react';

interface Officer {
  role_type: string;
  name: string;
  title: string;
  email: string;
  phone: string;
  appointed_date: string;
  notes: string;
}

const ROLES = [
  { key: 'privacy_officer', label: 'Privacy Officer', description: 'Responsible for developing and implementing HIPAA privacy policies (required by 164.530(a)(1))' },
  { key: 'security_officer', label: 'Security Officer', description: 'Responsible for developing and implementing HIPAA security policies (required by 164.308(a)(2))' },
];

export const OfficerDesignation: React.FC = () => {
  const [officers, setOfficers] = useState<Record<string, Officer>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => { fetchOfficers(); }, []);

  const fetchOfficers = async () => {
    const res = await fetch('/api/client/compliance/officers', { credentials: 'include' });
    if (res.ok) {
      const d = await res.json();
      const map: Record<string, Officer> = {};
      (d.officers || []).forEach((o: any) => {
        map[o.role_type] = {
          role_type: o.role_type,
          name: o.name || '',
          title: o.title || '',
          email: o.email || '',
          phone: o.phone || '',
          appointed_date: o.appointed_date || new Date().toISOString().split('T')[0],
          notes: o.notes || '',
        };
      });
      // Initialize empty officers for roles not yet assigned
      ROLES.forEach(r => {
        if (!map[r.key]) {
          map[r.key] = { role_type: r.key, name: '', title: '', email: '', phone: '', appointed_date: new Date().toISOString().split('T')[0], notes: '' };
        }
      });
      setOfficers(map);
    }
  };

  const updateOfficer = (roleType: string, field: string, value: string) => {
    setOfficers(prev => ({
      ...prev,
      [roleType]: { ...prev[roleType], [field]: value },
    }));
    setSaved(false);
  };

  const save = async () => {
    setSaving(true);
    const batch = Object.values(officers).filter(o => o.name.trim() !== '');
    await fetch('/api/client/compliance/officers', {
      method: 'PUT', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ officers: batch }),
    });
    setSaving(false);
    setSaved(true);
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-bold text-slate-900">Officer Designation</h2>
        <button onClick={save} disabled={saving} className="px-4 py-2 bg-teal-600 text-white rounded-lg hover:bg-teal-700 text-sm disabled:opacity-50">
          {saving ? 'Saving...' : saved ? 'Saved' : 'Save Officers'}
        </button>
      </div>

      <div className="space-y-6">
        {ROLES.map(role => {
          const officer = officers[role.key];
          if (!officer) return null;

          return (
            <div key={role.key} className="bg-white rounded-2xl border border-slate-100 p-6">
              <div className="flex items-center gap-3 mb-4">
                <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${role.key === 'privacy_officer' ? 'bg-purple-100' : 'bg-teal-100'}`}>
                  <svg className={`w-5 h-5 ${role.key === 'privacy_officer' ? 'text-purple-600' : 'text-teal-600'}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                  </svg>
                </div>
                <div>
                  <h3 className="font-semibold text-slate-900">{role.label}</h3>
                  <p className="text-xs text-slate-500">{role.description}</p>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Full Name *</label>
                  <input className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={officer.name} onChange={e => updateOfficer(role.key, 'name', e.target.value)} placeholder="Enter name" />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Title</label>
                  <input className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={officer.title} onChange={e => updateOfficer(role.key, 'title', e.target.value)} placeholder="e.g. Office Manager" />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Email</label>
                  <input type="email" className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={officer.email} onChange={e => updateOfficer(role.key, 'email', e.target.value)} />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Phone</label>
                  <input className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={officer.phone} onChange={e => updateOfficer(role.key, 'phone', e.target.value)} />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Appointed Date *</label>
                  <input type="date" className="w-full border border-slate-200 rounded-lg p-2 text-sm" value={officer.appointed_date} onChange={e => updateOfficer(role.key, 'appointed_date', e.target.value)} />
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-6 p-4 bg-blue-50 rounded-xl">
        <p className="text-sm text-blue-700">
          HIPAA requires designation of both a Privacy Officer and Security Officer. The same person may serve in both roles for small organizations.
        </p>
      </div>
    </div>
  );
};
