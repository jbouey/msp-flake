import React, { useState, useEffect } from 'react';
import { DocumentUpload } from './DocumentUpload';

interface SafeguardItem {
  category: string;
  item_key: string;
  description: string;
  status: string;
  hipaa_reference: string | null;
  notes: string | null;
  last_assessed: string | null;
  assessed_by: string | null;
}

const CATEGORY_LABELS: Record<string, string> = {
  facility_access: 'Facility Access Controls',
  workstation_use: 'Workstation Use',
  workstation_security: 'Workstation Security',
  device_media: 'Device & Media Controls',
};

const STATUS_OPTIONS = [
  { value: 'not_assessed', label: 'Not Assessed', color: 'bg-slate-100 text-slate-600' },
  { value: 'compliant', label: 'Compliant', color: 'bg-green-100 text-green-700' },
  { value: 'partial', label: 'Partial', color: 'bg-yellow-100 text-yellow-700' },
  { value: 'non_compliant', label: 'Non-Compliant', color: 'bg-red-100 text-red-700' },
  { value: 'not_applicable', label: 'N/A', color: 'bg-slate-50 text-slate-400' },
];

interface PhysicalSafeguardsProps {
  apiBase?: string;
}

export const PhysicalSafeguards: React.FC<PhysicalSafeguardsProps> = ({ apiBase = '/api/client/compliance' }) => {
  const [saving, setSaving] = useState(false);
  const [localItems, setLocalItems] = useState<Record<string, SafeguardItem>>({});

  useEffect(() => { fetchData(); }, []);

  const fetchData = async () => {
    const res = await fetch(`${apiBase}/physical`, { credentials: 'include' });
    if (res.ok) {
      const d = await res.json();
      // Merge existing data with template
      const merged: Record<string, SafeguardItem> = {};
      (d.template_items || []).forEach((t: SafeguardItem) => {
        const existing = (d.items || []).find((i: SafeguardItem) => i.category === t.category && i.item_key === t.item_key);
        const key = `${t.category}:${t.item_key}`;
        merged[key] = existing || { ...t, status: 'not_assessed', notes: null, last_assessed: null, assessed_by: null };
      });
      setLocalItems(merged);
    }
  };

  const updateItem = (key: string, field: string, value: string) => {
    setLocalItems(prev => ({
      ...prev,
      [key]: { ...prev[key], [field]: value },
    }));
  };

  const save = async () => {
    setSaving(true);
    const batch = Object.values(localItems).map(i => ({
      category: i.category,
      item_key: i.item_key,
      description: i.description,
      status: i.status,
      hipaa_reference: i.hipaa_reference,
      notes: i.notes,
    }));
    await fetch(`${apiBase}/physical`, {
      method: 'PUT', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: batch }),
    });
    setSaving(false);
    await fetchData();
  };

  const categories = Object.keys(CATEGORY_LABELS);
  const totalItems = Object.keys(localItems).length;
  const assessed = Object.values(localItems).filter(i => i.status !== 'not_assessed').length;
  const compliant = Object.values(localItems).filter(i => i.status === 'compliant').length;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-xl font-bold text-slate-900">Physical Safeguards Checklist</h2>
          <p className="text-sm text-slate-500 mt-1">{assessed}/{totalItems} assessed | {compliant} compliant</p>
        </div>
        <button onClick={save} disabled={saving} className="px-4 py-2 bg-teal-600 text-white rounded-lg hover:bg-teal-700 text-sm disabled:opacity-50">
          {saving ? 'Saving...' : 'Save Assessment'}
        </button>
      </div>

      {/* Progress */}
      <div className="bg-white rounded-2xl border border-slate-100 p-4 mb-6">
        <div className="h-2 bg-slate-100 rounded-full">
          <div className="h-full bg-teal-500 rounded-full transition-all" style={{ width: `${totalItems > 0 ? (assessed / totalItems) * 100 : 0}%` }} />
        </div>
      </div>

      {categories.map(cat => {
        const catItems = Object.entries(localItems).filter(([k]) => k.startsWith(cat + ':'));
        if (catItems.length === 0) return null;

        return (
          <div key={cat} className="bg-white rounded-2xl border border-slate-100 p-6 mb-4">
            <h3 className="font-semibold text-slate-900 mb-4">{CATEGORY_LABELS[cat]}</h3>
            <div className="space-y-3">
              {catItems.map(([key, item]) => (
                <div key={key} className="flex items-start gap-4 p-3 rounded-xl bg-slate-50/50 border border-slate-100">
                  <div className="flex-1">
                    <p className="text-sm text-slate-900">{item.description}</p>
                    {item.hipaa_reference && (
                      <span className="text-xs text-slate-400">{item.hipaa_reference}</span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <select
                      className={`text-xs rounded-lg border-0 py-1 px-2 ${STATUS_OPTIONS.find(s => s.value === item.status)?.color || ''}`}
                      value={item.status}
                      onChange={e => updateItem(key, 'status', e.target.value)}
                    >
                      {STATUS_OPTIONS.map(s => (
                        <option key={s.value} value={s.value}>{s.label}</option>
                      ))}
                    </select>
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      })}

      <DocumentUpload moduleKey="physical" apiBase={apiBase} />
    </div>
  );
};
