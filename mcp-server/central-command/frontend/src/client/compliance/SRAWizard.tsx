import React, { useState, useEffect } from 'react';

interface Question {
  key: string;
  category: string;
  hipaa_reference: string;
  text: string;
}

interface SRAResponse {
  question_key: string;
  response: string | null;
  risk_level: string;
  remediation_plan: string | null;
  remediation_due: string | null;
  notes: string | null;
}

interface Assessment {
  id: string;
  title: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  overall_risk_score: number | null;
  total_questions: number;
  answered_questions: number;
  findings_count: number;
}

const RISK_OPTIONS = [
  { value: 'not_assessed', label: 'Not Assessed', color: 'bg-slate-100 text-slate-600' },
  { value: 'low', label: 'Low Risk', color: 'bg-green-100 text-green-700' },
  { value: 'medium', label: 'Medium Risk', color: 'bg-yellow-100 text-yellow-700' },
  { value: 'high', label: 'High Risk', color: 'bg-orange-100 text-orange-700' },
  { value: 'critical', label: 'Critical', color: 'bg-red-100 text-red-700' },
];

const RESPONSE_OPTIONS = [
  { value: 'fully_implemented', label: 'Fully Implemented', riskMap: 'low' },
  { value: 'partially_implemented', label: 'Partially Implemented', riskMap: 'medium' },
  { value: 'not_implemented', label: 'Not Implemented', riskMap: 'high' },
  { value: 'not_applicable', label: 'N/A', riskMap: 'not_assessed' },
];

const CATEGORIES = ['administrative', 'physical', 'technical'];
const CATEGORY_LABELS: Record<string, string> = {
  administrative: 'Administrative Safeguards',
  physical: 'Physical Safeguards',
  technical: 'Technical Safeguards',
};

interface SRAWizardProps {
  apiBase?: string;
}

export const SRAWizard: React.FC<SRAWizardProps> = ({ apiBase = '/api/client/compliance' }) => {
  const [assessments, setAssessments] = useState<Assessment[]>([]);
  const [activeAssessment, setActiveAssessment] = useState<Assessment | null>(null);
  const [questions, setQuestions] = useState<Question[]>([]);
  const [responses, setResponses] = useState<Record<string, SRAResponse>>({});
  const [step, setStep] = useState(0); // 0=list, 1-3=categories, 4=summary, 5=remediation
  const [saving, setSaving] = useState(false);
  const [expandedNotes, setExpandedNotes] = useState<Set<string>>(new Set());

  useEffect(() => { fetchAssessments(); }, []);

  const fetchAssessments = async () => {
    const res = await fetch(`${apiBase}/sra`, { credentials: 'include' });
    if (res.ok) {
      const data = await res.json();
      setAssessments(data.assessments || []);
    }
  };

  const loadAssessment = async (id: string) => {
    const res = await fetch(`${apiBase}/sra/${id}`, { credentials: 'include' });
    if (res.ok) {
      const data = await res.json();
      setActiveAssessment(data.assessment);
      setQuestions(data.questions || []);
      const rMap: Record<string, SRAResponse> = {};
      (data.responses || []).forEach((r: any) => {
        rMap[r.question_key] = r;
      });
      setResponses(rMap);
      setStep(1);
    }
  };

  const startNew = async () => {
    const res = await fetch(`${apiBase}/sra`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'Annual Security Risk Assessment' }),
    });
    if (res.ok) {
      const data = await res.json();
      await loadAssessment(data.id);
    }
  };

  const updateResponse = (key: string, field: string, value: string) => {
    setResponses(prev => ({
      ...prev,
      [key]: { ...prev[key], question_key: key, [field]: value } as SRAResponse,
    }));
  };

  const handleResponseChoice = (key: string, choice: string) => {
    const option = RESPONSE_OPTIONS.find(o => o.value === choice);
    setResponses(prev => ({
      ...prev,
      [key]: {
        ...prev[key],
        question_key: key,
        response: choice,
        risk_level: option?.riskMap || 'not_assessed',
      } as SRAResponse,
    }));
  };

  const saveProgress = async () => {
    if (!activeAssessment) return;
    setSaving(true);
    const batch = Object.values(responses).map(r => ({
      question_key: r.question_key,
      response: r.response,
      risk_level: r.risk_level,
      remediation_plan: r.remediation_plan,
      remediation_due: r.remediation_due,
      notes: r.notes,
    }));
    await fetch(`${apiBase}/sra/${activeAssessment.id}/responses`, {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ responses: batch }),
    });
    setSaving(false);
  };

  const completeAssessment = async () => {
    if (!activeAssessment) return;
    await saveProgress();
    const res = await fetch(`${apiBase}/sra/${activeAssessment.id}/complete`, {
      method: 'POST',
      credentials: 'include',
    });
    if (res.ok) {
      const data = await res.json();
      setActiveAssessment(data);
      setStep(4);
    }
  };

  const toggleNotes = (key: string) => {
    setExpandedNotes(prev => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  };

  // List view
  if (step === 0) {
    return (
      <div>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-bold text-slate-900">Security Risk Assessments</h2>
          <button onClick={startNew} className="px-4 py-2 bg-teal-600 text-white rounded-lg hover:bg-teal-700 text-sm font-medium">
            Start New Assessment
          </button>
        </div>
        <div className="mb-6 p-4 bg-teal-50/60 rounded-xl border border-teal-100">
          <p className="text-sm font-medium text-teal-900 mb-1">What is this?</p>
          <p className="text-sm text-teal-800">A Security Risk Assessment (SRA) identifies where your practice may be vulnerable to a breach of patient data. HIPAA requires one annually.</p>
          <p className="text-sm font-medium text-teal-900 mt-3 mb-1">How to complete it</p>
          <p className="text-sm text-teal-800">Click "Start New Assessment" and answer each question honestly. For each item, select whether it's fully implemented, partially implemented, or not yet in place. The system will calculate your overall risk score and flag areas that need attention. Plan to set aside 30-60 minutes.</p>
        </div>
        {assessments.length === 0 ? (
          <div className="bg-white rounded-2xl border border-slate-100 p-12 text-center">
            <svg className="w-12 h-12 text-slate-300 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
            </svg>
            <p className="text-slate-600 mb-4">No risk assessments yet. HIPAA requires an annual SRA.</p>
            <button onClick={startNew} className="px-4 py-2 bg-teal-600 text-white rounded-lg hover:bg-teal-700 text-sm">
              Start Your First Assessment
            </button>
          </div>
        ) : (
          <div className="space-y-4">
            {assessments.map(a => (
              <div key={a.id} className="bg-white rounded-2xl border border-slate-100 p-6 flex items-center justify-between">
                <div>
                  <h3 className="font-semibold text-slate-900">{a.title}</h3>
                  <p className="text-sm text-slate-500 mt-1">
                    Started {new Date(a.started_at).toLocaleDateString()} | {a.answered_questions}/{a.total_questions} answered
                    {a.findings_count > 0 && ` | ${a.findings_count} findings`}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  {a.status === 'completed' && a.overall_risk_score !== null && (
                    <span className={`text-lg font-bold ${a.overall_risk_score > 50 ? 'text-red-600' : a.overall_risk_score > 25 ? 'text-yellow-600' : 'text-green-600'}`}>
                      Risk: {a.overall_risk_score}
                    </span>
                  )}
                  <span className={`px-3 py-1 text-xs font-medium rounded-full ${a.status === 'completed' ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'}`}>
                    {a.status === 'completed' ? 'Completed' : 'In Progress'}
                  </span>
                  <button onClick={() => loadAssessment(a.id)} className="px-3 py-1.5 text-sm text-teal-600 hover:bg-teal-50 rounded-lg">
                    {a.status === 'completed' ? 'Review' : 'Continue'}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  // Wizard steps
  const currentCategory = CATEGORIES[step - 1];
  const categoryQuestions = currentCategory ? questions.filter(q => q.category === currentCategory) : [];
  const allAnswered = questions.every(q => responses[q.key]?.response);
  const findings = Object.values(responses).filter(r => r.risk_level === 'high' || r.risk_level === 'critical');

  return (
    <div>
      {/* Progress bar */}
      <div className="bg-white rounded-2xl border border-slate-100 p-4 mb-6">
        <div className="flex items-center gap-2 mb-2">
          {[1, 2, 3, 4, 5].map(s => (
            <button
              key={s}
              onClick={() => setStep(s <= 3 || (s === 4 && allAnswered) ? s : step)}
              className={`flex-1 h-2 rounded-full transition-all ${step >= s ? 'bg-teal-500' : 'bg-slate-200'}`}
            />
          ))}
        </div>
        <div className="flex justify-between text-xs text-slate-500">
          <span className={step === 1 ? 'text-teal-600 font-medium' : ''}>Administrative</span>
          <span className={step === 2 ? 'text-teal-600 font-medium' : ''}>Physical</span>
          <span className={step === 3 ? 'text-teal-600 font-medium' : ''}>Technical</span>
          <span className={step === 4 ? 'text-teal-600 font-medium' : ''}>Summary</span>
          <span className={step === 5 ? 'text-teal-600 font-medium' : ''}>Remediation</span>
        </div>
      </div>

      {/* Summary step */}
      {step === 4 && activeAssessment && (
        <div className="bg-white rounded-2xl border border-slate-100 p-8">
          <h2 className="text-xl font-bold text-slate-900 mb-6">Risk Assessment Summary</h2>
          {activeAssessment.overall_risk_score !== null && (
            <div className="mb-6 p-6 rounded-xl bg-slate-50">
              <p className="text-sm text-slate-500 mb-1">Overall Risk Score</p>
              <p className={`text-4xl font-bold ${activeAssessment.overall_risk_score > 50 ? 'text-red-600' : activeAssessment.overall_risk_score > 25 ? 'text-yellow-600' : 'text-green-600'}`}>
                {activeAssessment.overall_risk_score}
              </p>
            </div>
          )}
          <div className="grid grid-cols-4 gap-4 mb-6">
            {RISK_OPTIONS.filter(r => r.value !== 'not_assessed').map(r => {
              const count = Object.values(responses).filter(resp => resp.risk_level === r.value).length;
              return (
                <div key={r.value} className="p-4 rounded-xl bg-slate-50 text-center">
                  <p className="text-2xl font-bold text-slate-900">{count}</p>
                  <p className="text-xs text-slate-500">{r.label}</p>
                </div>
              );
            })}
          </div>
          {findings.length > 0 && (
            <div>
              <h3 className="font-semibold text-slate-900 mb-3">High/Critical Findings</h3>
              {findings.map(f => {
                const q = questions.find(q => q.key === f.question_key);
                return (
                  <div key={f.question_key} className="p-4 border border-red-100 rounded-lg mb-2 bg-red-50/50">
                    <p className="text-sm text-slate-900">{q?.text}</p>
                    <span className={`mt-1 inline-block px-2 py-0.5 text-xs rounded-full ${f.risk_level === 'critical' ? 'bg-red-100 text-red-700' : 'bg-orange-100 text-orange-700'}`}>
                      {f.risk_level}
                    </span>
                  </div>
                );
              })}
              <button onClick={() => setStep(5)} className="mt-4 px-4 py-2 bg-teal-600 text-white rounded-lg hover:bg-teal-700 text-sm">
                Create Remediation Plan
              </button>
            </div>
          )}
          {activeAssessment.status !== 'completed' && (
            <button onClick={completeAssessment} className="mt-6 px-6 py-2.5 bg-green-600 text-white rounded-lg hover:bg-green-700 font-medium">
              Complete Assessment
            </button>
          )}
        </div>
      )}

      {/* Remediation step */}
      {step === 5 && (
        <div className="bg-white rounded-2xl border border-slate-100 p-8">
          <h2 className="text-xl font-bold text-slate-900 mb-6">Remediation Plan</h2>
          {findings.length === 0 ? (
            <p className="text-slate-500">No high or critical findings to remediate.</p>
          ) : (
            <div className="space-y-4">
              {findings.map(f => {
                const q = questions.find(q => q.key === f.question_key);
                return (
                  <div key={f.question_key} className="p-4 border border-slate-200 rounded-xl">
                    <p className="text-sm font-medium text-slate-900 mb-3">{q?.text}</p>
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <label className="block text-xs text-slate-500 mb-1">Remediation Plan</label>
                        <textarea
                          className="w-full border border-slate-200 rounded-lg p-2 text-sm"
                          rows={2}
                          value={f.remediation_plan || ''}
                          onChange={e => updateResponse(f.question_key, 'remediation_plan', e.target.value)}
                          placeholder="Describe corrective actions..."
                        />
                      </div>
                      <div>
                        <label className="block text-xs text-slate-500 mb-1">Due Date</label>
                        <input
                          type="date"
                          className="w-full border border-slate-200 rounded-lg p-2 text-sm"
                          value={f.remediation_due || ''}
                          onChange={e => updateResponse(f.question_key, 'remediation_due', e.target.value)}
                        />
                      </div>
                    </div>
                  </div>
                );
              })}
              <button onClick={saveProgress} disabled={saving} className="px-4 py-2 bg-teal-600 text-white rounded-lg hover:bg-teal-700 text-sm disabled:opacity-50">
                {saving ? 'Saving...' : 'Save Remediation Plan'}
              </button>
            </div>
          )}
        </div>
      )}

      {/* Question steps (1-3) */}
      {step >= 1 && step <= 3 && (
        <div>
          <h2 className="text-xl font-bold text-slate-900 mb-2">{CATEGORY_LABELS[currentCategory]}</h2>
          <p className="text-sm text-slate-500 mb-6">{categoryQuestions.length} questions in this section</p>

          <div className="space-y-4">
            {categoryQuestions.map((q, idx) => {
              const resp = responses[q.key];
              return (
                <div key={q.key} className="bg-white rounded-2xl border border-slate-100 p-6">
                  <div className="flex items-start justify-between mb-4">
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-2">
                        <span className="text-xs font-mono text-slate-400">Q{idx + 1}</span>
                        <span className="px-2 py-0.5 text-xs rounded-full bg-slate-100 text-slate-500">{q.hipaa_reference}</span>
                      </div>
                      <p className="text-sm text-slate-900">{q.text}</p>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-2 mb-3">
                    {RESPONSE_OPTIONS.map(opt => (
                      <button
                        key={opt.value}
                        onClick={() => handleResponseChoice(q.key, opt.value)}
                        className={`px-3 py-1.5 text-xs font-medium rounded-lg border transition-all ${
                          resp?.response === opt.value
                            ? 'border-teal-500 bg-teal-50 text-teal-700'
                            : 'border-slate-200 text-slate-600 hover:border-slate-300'
                        }`}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>

                  {resp?.risk_level && resp.risk_level !== 'not_assessed' && (
                    <span className={`inline-block px-2 py-0.5 text-xs rounded-full ${RISK_OPTIONS.find(r => r.value === resp.risk_level)?.color || ''}`}>
                      {RISK_OPTIONS.find(r => r.value === resp.risk_level)?.label}
                    </span>
                  )}

                  <button
                    onClick={() => toggleNotes(q.key)}
                    className="ml-2 text-xs text-slate-400 hover:text-slate-600"
                  >
                    {expandedNotes.has(q.key) ? 'Hide notes' : 'Add notes'}
                  </button>

                  {expandedNotes.has(q.key) && (
                    <textarea
                      className="mt-2 w-full border border-slate-200 rounded-lg p-2 text-sm"
                      rows={2}
                      placeholder="Additional notes..."
                      value={resp?.notes || ''}
                      onChange={e => updateResponse(q.key, 'notes', e.target.value)}
                    />
                  )}
                </div>
              );
            })}
          </div>

          {/* Navigation */}
          <div className="flex items-center justify-between mt-6">
            <button
              onClick={() => { saveProgress(); setStep(step - 1); }}
              className="px-4 py-2 text-sm text-slate-600 hover:bg-slate-100 rounded-lg"
            >
              {step === 1 ? 'Back to List' : 'Previous'}
            </button>
            <div className="flex gap-2">
              <button onClick={saveProgress} disabled={saving} className="px-4 py-2 text-sm border border-slate-200 rounded-lg hover:bg-slate-50 disabled:opacity-50">
                {saving ? 'Saving...' : 'Save Progress'}
              </button>
              <button
                onClick={() => { saveProgress(); setStep(step + 1); }}
                className="px-4 py-2 text-sm bg-teal-600 text-white rounded-lg hover:bg-teal-700"
              >
                {step === 3 ? 'View Summary' : 'Next Section'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
