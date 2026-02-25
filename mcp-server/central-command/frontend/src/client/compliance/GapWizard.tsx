import React, { useState, useEffect } from 'react';

interface Question {
  section: string;
  question_key: string;
  hipaa_reference: string;
  text: string;
}

interface GapResponse {
  question_key: string;
  section: string;
  hipaa_reference: string;
  response: string | null;
  maturity_level: number;
  notes: string | null;
  evidence_ref: string | null;
}

const SECTIONS = ['administrative', 'physical', 'technical', 'organizational'];
const SECTION_LABELS: Record<string, string> = {
  administrative: 'Administrative Safeguards',
  physical: 'Physical Safeguards',
  technical: 'Technical Safeguards',
  organizational: 'Organizational Requirements',
};

const RESPONSE_OPTIONS = [
  { value: 'yes', label: 'Yes', color: 'bg-green-100 text-green-700' },
  { value: 'partial', label: 'Partial', color: 'bg-yellow-100 text-yellow-700' },
  { value: 'no', label: 'No', color: 'bg-red-100 text-red-700' },
  { value: 'not_applicable', label: 'N/A', color: 'bg-slate-100 text-slate-500' },
];

const MATURITY_LEVELS = [
  { value: 0, label: 'Not Started' },
  { value: 1, label: 'Ad Hoc' },
  { value: 2, label: 'Repeatable' },
  { value: 3, label: 'Defined' },
  { value: 4, label: 'Managed' },
  { value: 5, label: 'Optimized' },
];

interface GapWizardProps {
  apiBase?: string;
}

export const GapWizard: React.FC<GapWizardProps> = ({ apiBase = '/api/client/compliance' }) => {
  const [questions, setQuestions] = useState<Question[]>([]);
  const [responses, setResponses] = useState<Record<string, GapResponse>>({});
  const [activeSection, setActiveSection] = useState<string>('administrative');
  const [saving, setSaving] = useState(false);
  const [showReport, setShowReport] = useState(false);

  useEffect(() => { fetchData(); }, []);

  const fetchData = async () => {
    const res = await fetch(`${apiBase}/gap-analysis`, { credentials: 'include' });
    if (res.ok) {
      const d = await res.json();
      setQuestions(d.questions || []);
      const rMap: Record<string, GapResponse> = {};
      (d.responses || []).forEach((r: any) => { rMap[r.question_key] = r; });
      setResponses(rMap);
    }
  };

  const updateResponse = (key: string, field: string, value: any) => {
    const q = questions.find(q => q.question_key === key);
    setResponses(prev => ({
      ...prev,
      [key]: {
        ...prev[key],
        question_key: key,
        section: q?.section || '',
        hipaa_reference: q?.hipaa_reference || '',
        [field]: value,
      } as GapResponse,
    }));
  };

  const save = async () => {
    setSaving(true);
    const batch = Object.values(responses).filter(r => r.response !== null);
    await fetch(`${apiBase}/gap-analysis`, {
      method: 'PUT', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ responses: batch }),
    });
    setSaving(false);
  };

  const sectionQuestions = questions.filter(q => q.section === activeSection);
  const totalAnswered = Object.values(responses).filter(r => r.response).length;
  const totalQuestions = questions.length;
  const completion = totalQuestions > 0 ? Math.round((totalAnswered / totalQuestions) * 100) : 0;

  const maturityAvg = (() => {
    const rated = Object.values(responses).filter(r => r.maturity_level > 0);
    if (rated.length === 0) return 0;
    return rated.reduce((sum, r) => sum + r.maturity_level, 0) / rated.length;
  })();

  // Report view
  if (showReport) {
    const sectionScores = SECTIONS.map(section => {
      const secQuestions = questions.filter(q => q.section === section);
      const secResponses = secQuestions.map(q => responses[q.question_key]).filter(Boolean);
      const yes = secResponses.filter(r => r.response === 'yes').length;
      const partial = secResponses.filter(r => r.response === 'partial').length;
      const no = secResponses.filter(r => r.response === 'no').length;
      const score = secQuestions.length > 0 ? Math.round(((yes + partial * 0.5) / secQuestions.length) * 100) : 0;
      return { section, label: SECTION_LABELS[section], yes, partial, no, total: secQuestions.length, score };
    });

    const gaps = questions.filter(q => {
      const r = responses[q.question_key];
      return r?.response === 'no' || r?.response === 'partial';
    });

    return (
      <div>
        <button onClick={() => setShowReport(false)} className="mb-4 text-sm text-slate-500 hover:text-teal-600">&larr; Back to questionnaire</button>
        <div className="bg-white rounded-2xl border border-slate-100 p-8 mb-6">
          <h2 className="text-xl font-bold text-slate-900 mb-6">Gap Analysis Report</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
            <div className="p-4 bg-slate-50 rounded-xl text-center">
              <p className="text-3xl font-bold text-slate-900">{completion}%</p>
              <p className="text-xs text-slate-500">Completion</p>
            </div>
            <div className="p-4 bg-slate-50 rounded-xl text-center">
              <p className="text-3xl font-bold text-slate-900">{maturityAvg.toFixed(1)}</p>
              <p className="text-xs text-slate-500">Avg Maturity (0-5)</p>
            </div>
            <div className="p-4 bg-slate-50 rounded-xl text-center">
              <p className="text-3xl font-bold text-green-600">{Object.values(responses).filter(r => r.response === 'yes').length}</p>
              <p className="text-xs text-slate-500">Compliant</p>
            </div>
            <div className="p-4 bg-slate-50 rounded-xl text-center">
              <p className="text-3xl font-bold text-red-600">{gaps.length}</p>
              <p className="text-xs text-slate-500">Gaps Found</p>
            </div>
          </div>

          <h3 className="font-semibold text-slate-900 mb-3">Section Scores</h3>
          <div className="space-y-3 mb-8">
            {sectionScores.map(s => (
              <div key={s.section} className="flex items-center gap-4">
                <span className="text-sm text-slate-700 w-48">{s.label}</span>
                <div className="flex-1 h-3 bg-slate-100 rounded-full">
                  <div className={`h-full rounded-full ${s.score >= 80 ? 'bg-green-500' : s.score >= 50 ? 'bg-yellow-500' : 'bg-red-500'}`} style={{ width: `${s.score}%` }} />
                </div>
                <span className="text-sm font-medium text-slate-900 w-12 text-right">{s.score}%</span>
              </div>
            ))}
          </div>

          {gaps.length > 0 && (
            <div>
              <h3 className="font-semibold text-slate-900 mb-3">Identified Gaps</h3>
              <div className="space-y-2">
                {gaps.map(g => {
                  const r = responses[g.question_key];
                  return (
                    <div key={g.question_key} className="p-3 rounded-lg bg-red-50/50 border border-red-100">
                      <div className="flex items-start justify-between">
                        <p className="text-sm text-slate-900">{g.text}</p>
                        <span className={`ml-2 px-2 py-0.5 text-xs rounded-full ${r?.response === 'no' ? 'bg-red-100 text-red-700' : 'bg-yellow-100 text-yellow-700'}`}>
                          {r?.response === 'no' ? 'Not Met' : 'Partial'}
                        </span>
                      </div>
                      <span className="text-xs text-slate-400">{g.hipaa_reference}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900">HIPAA Gap Analysis</h2>
          <p className="text-sm text-slate-500 mt-1">{totalAnswered}/{totalQuestions} answered ({completion}%)</p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => setShowReport(true)} className="px-4 py-2 text-sm border border-slate-200 rounded-lg hover:bg-slate-50">
            View Report
          </button>
          <button onClick={save} disabled={saving} className="px-4 py-2 bg-teal-600 text-white rounded-lg hover:bg-teal-700 text-sm disabled:opacity-50">
            {saving ? 'Saving...' : 'Save Progress'}
          </button>
        </div>
      </div>

      <div className="mb-6 px-6 py-5 bg-teal-50/60 rounded-2xl border border-teal-100">
        <p className="text-sm font-medium text-teal-900 mb-1">What is this?</p>
        <p className="text-sm text-teal-800 leading-relaxed">A gap analysis is a self-assessment that compares your current practices against HIPAA requirements. It shows where you're in good shape and where you have gaps to close. This is different from the SRA — the SRA focuses on risk levels, while the gap analysis checks whether specific safeguards exist at all.</p>
        <p className="text-sm font-medium text-teal-900 mt-3 mb-1">How to complete it</p>
        <p className="text-sm text-teal-800 leading-relaxed">Work through each section (Administrative, Physical, Technical, Organizational). For each question, select Yes, Partial, or No. Then rate your maturity level — this helps track improvement over time. Click "View Report" when finished to see your scores and identified gaps. Save your progress at any time — you don't have to finish in one sitting.</p>
      </div>

      {/* Progress */}
      <div className="bg-white rounded-2xl border border-slate-100 p-4 mb-6">
        <div className="h-2 bg-slate-100 rounded-full">
          <div className="h-full bg-teal-500 rounded-full transition-all" style={{ width: `${completion}%` }} />
        </div>
      </div>

      {/* Section tabs */}
      <div className="flex gap-2 mb-6 overflow-x-auto">
        {SECTIONS.map(s => {
          const answered = questions.filter(q => q.section === s && responses[q.question_key]?.response).length;
          const total = questions.filter(q => q.section === s).length;
          return (
            <button key={s} onClick={() => setActiveSection(s)}
              className={`px-3 py-1.5 text-sm rounded-lg whitespace-nowrap ${activeSection === s ? 'bg-teal-100 text-teal-700 font-medium' : 'text-slate-500 hover:bg-slate-100'}`}>
              {SECTION_LABELS[s]} ({answered}/{total})
            </button>
          );
        })}
      </div>

      {/* Questions */}
      <div className="space-y-3">
        {sectionQuestions.map((q, idx) => {
          const resp = responses[q.question_key];
          return (
            <div key={q.question_key} className="bg-white rounded-2xl border border-slate-100 p-5">
              <div className="flex items-start gap-3 mb-3">
                <span className="text-xs font-mono text-slate-400 mt-0.5">Q{idx + 1}</span>
                <div className="flex-1">
                  <p className="text-sm text-slate-900">{q.text}</p>
                  <span className="text-xs text-slate-400">{q.hipaa_reference}</span>
                </div>
              </div>

              <div className="flex items-center gap-4 ml-7">
                <div className="flex gap-1.5">
                  {RESPONSE_OPTIONS.map(opt => (
                    <button key={opt.value} onClick={() => updateResponse(q.question_key, 'response', opt.value)}
                      className={`px-2.5 py-1 text-xs rounded-lg border transition-all ${resp?.response === opt.value ? 'border-teal-500 bg-teal-50 text-teal-700' : 'border-slate-200 text-slate-500 hover:border-slate-300'}`}>
                      {opt.label}
                    </button>
                  ))}
                </div>

                <select className="text-xs border border-slate-200 rounded-lg p-1" value={resp?.maturity_level || 0}
                  onChange={e => updateResponse(q.question_key, 'maturity_level', parseInt(e.target.value))}>
                  {MATURITY_LEVELS.map(m => (
                    <option key={m.value} value={m.value}>{m.value} - {m.label}</option>
                  ))}
                </select>
              </div>
            </div>
          );
        })}
      </div>

      <div className="flex justify-end mt-6">
        <button onClick={save} disabled={saving} className="px-4 py-2 bg-teal-600 text-white rounded-lg hover:bg-teal-700 text-sm disabled:opacity-50">
          {saving ? 'Saving...' : 'Save Progress'}
        </button>
      </div>
    </div>
  );
};
