import React, { useState, useEffect, useMemo } from 'react';

// ─── Types ───────────────────────────────────────────────────────────────────

interface CategoryBreakdown {
  patching: number | null;
  antivirus: number | null;
  backup: number | null;
  logging: number | null;
  firewall: number | null;
  encryption: number | null;
  access_control: number | null;
  services: number | null;
}

interface TrendPoint {
  date: string;
  score: number;
}

interface HealingStats {
  total: number;
  auto_healed: number;
  pending: number;
}

interface ComplianceHealthData {
  site_id: string;
  clinic_name: string;
  overall_score: number | null;
  breakdown: CategoryBreakdown;
  counts: { passed: number; failed: number; warnings: number; total: number };
  trend: TrendPoint[];
  healing: HealingStats;
}

interface Site {
  site_id: string;
  clinic_name: string;
}

interface Props {
  sites: Site[];
}

// ─── Category Metadata ──────────────────────────────────────────────────────

const CATEGORIES: { key: keyof CategoryBreakdown; label: string; icon: string }[] = [
  { key: 'encryption',      label: 'Encryption',      icon: 'M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z' },
  { key: 'firewall',        label: 'Firewall',        icon: 'M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z' },
  { key: 'access_control',  label: 'Access Control',  icon: 'M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z' },
  { key: 'patching',        label: 'Patching',        icon: 'M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15' },
  { key: 'logging',         label: 'Logging',         icon: 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01' },
  { key: 'services',        label: 'Services',        icon: 'M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2m-2-4h.01M17 16h.01' },
  { key: 'backup',          label: 'Backup',          icon: 'M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4' },
  { key: 'antivirus',       label: 'Antivirus',       icon: 'M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4' },
];

// ─── Color Helpers ──────────────────────────────────────────────────────────

function scoreColor(score: number | null): string {
  if (score === null) return '#8E8E93';
  if (score >= 90) return '#34C759';
  if (score >= 75) return '#14A89E';
  if (score >= 50) return '#FF9500';
  return '#FF3B30';
}

function scoreLabel(score: number | null): string {
  if (score === null) return 'No Data';
  if (score >= 90) return 'Excellent';
  if (score >= 75) return 'Good';
  if (score >= 50) return 'Needs Attention';
  return 'Critical';
}

// ─── Animated Circular Gauge (SVG) ─────────────────────────────────────────

const CircularGauge: React.FC<{ score: number | null; size?: number }> = ({ score, size = 200 }) => {
  const [animatedScore, setAnimatedScore] = useState(0);
  const r = (size - 24) / 2;
  const circumference = 2 * Math.PI * r;
  const displayScore = score ?? 0;
  const offset = circumference - (animatedScore / 100) * circumference;
  const color = scoreColor(score);

  useEffect(() => {
    let frame: number;
    const start = window.performance.now();
    const duration = 1200;
    const from = 0;
    const to = displayScore;

    const animate = (now: number) => {
      const elapsed = now - start;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setAnimatedScore(from + (to - from) * eased);
      if (progress < 1) frame = window.requestAnimationFrame(animate);
    };

    frame = window.requestAnimationFrame(animate);
    return () => window.cancelAnimationFrame(frame);
  }, [displayScore]);

  const center = size / 2;

  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="transform -rotate-90">
        {/* Background track */}
        <circle
          cx={center} cy={center} r={r}
          fill="none"
          stroke="rgba(120,120,128,0.08)"
          strokeWidth={12}
        />
        {/* Score arc */}
        <circle
          cx={center} cy={center} r={r}
          fill="none"
          stroke={color}
          strokeWidth={12}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          style={{ transition: 'stroke 0.6s ease' }}
        />
        {/* Subtle glow */}
        <circle
          cx={center} cy={center} r={r}
          fill="none"
          stroke={color}
          strokeWidth={12}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          style={{ filter: 'blur(6px)', opacity: 0.3 }}
        />
      </svg>

      {/* Center content */}
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        {/* Shield icon */}
        <svg className="w-6 h-6 mb-1" style={{ color }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
            d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
        </svg>
        <span className="text-3xl font-bold tabular-nums" style={{ color, fontFamily: "'Plus Jakarta Sans', sans-serif" }}>
          {score !== null ? Math.round(animatedScore) : '—'}
        </span>
        <span className="text-xs font-medium mt-0.5" style={{ color: 'var(--label-tertiary)' }}>
          {score !== null ? scoreLabel(score) : 'Awaiting data'}
        </span>
      </div>
    </div>
  );
};

// ─── Category Ring Segment ──────────────────────────────────────────────────

const CategoryRing: React.FC<{
  categories: { key: string; label: string; score: number | null }[];
  size?: number;
}> = ({ categories, size = 260 }) => {
  const center = size / 2;
  const r = (size - 16) / 2;
  const gap = 3; // degrees gap between segments
  const totalGap = gap * categories.length;
  const availableDeg = 360 - totalGap;
  const segDeg = availableDeg / categories.length;
  const circumference = 2 * Math.PI * r;

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="absolute inset-0 m-auto">
      {categories.map((cat, i) => {
        const segLength = (segDeg / 360) * circumference;
        const color = scoreColor(cat.score);
        const opacity = cat.score !== null ? 1 : 0.2;

        return (
          <circle
            key={cat.key}
            cx={center} cy={center} r={r}
            fill="none"
            stroke={color}
            strokeWidth={6}
            strokeLinecap="round"
            strokeDasharray={`${segLength} ${circumference - segLength}`}
            strokeDashoffset={-((i * (segDeg + gap)) / 360) * circumference}
            opacity={opacity}
            style={{
              transformOrigin: `${center}px ${center}px`,
              transform: 'rotate(-90deg)',
              transition: 'stroke 0.6s ease, opacity 0.6s ease',
            }}
          />
        );
      })}
    </svg>
  );
};

// ─── Sparkline ──────────────────────────────────────────────────────────────

const Sparkline: React.FC<{ data: TrendPoint[]; width?: number; height?: number }> = ({
  data,
  width = 200,
  height = 48,
}) => {
  if (data.length < 2) {
    return (
      <div className="flex items-center justify-center text-xs" style={{ width, height, color: 'var(--label-tertiary)' }}>
        Collecting trend data...
      </div>
    );
  }

  const scores = data.map(d => d.score);
  const min = Math.min(...scores, 0);
  const max = Math.max(...scores, 100);
  const range = max - min || 1;
  const padding = 4;
  const innerW = width - padding * 2;
  const innerH = height - padding * 2;

  const points = data.map((d, i) => {
    const x = padding + (i / (data.length - 1)) * innerW;
    const y = padding + innerH - ((d.score - min) / range) * innerH;
    return `${x},${y}`;
  });

  const pathD = `M${points.join(' L')}`;
  const areaD = `${pathD} L${padding + innerW},${height} L${padding},${height} Z`;

  // Trend direction
  const first = data[0].score;
  const last = data[data.length - 1].score;
  const trendColor = last >= first ? '#34C759' : '#FF3B30';

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
      <defs>
        <linearGradient id="sparkGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={trendColor} stopOpacity={0.15} />
          <stop offset="100%" stopColor={trendColor} stopOpacity={0} />
        </linearGradient>
      </defs>
      <path d={areaD} fill="url(#sparkGrad)" />
      <path d={pathD} fill="none" stroke={trendColor} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
      {/* End dot */}
      {data.length > 0 && (() => {
        const lastX = padding + ((data.length - 1) / (data.length - 1)) * innerW;
        const lastY = padding + innerH - ((last - min) / range) * innerH;
        return <circle cx={lastX} cy={lastY} r={2.5} fill={trendColor} />;
      })()}
    </svg>
  );
};

// ─── Category Card (mini) ───────────────────────────────────────────────────

const CategoryCard: React.FC<{
  label: string;
  icon: string;
  score: number | null;
  delay: number;
}> = ({ label, icon, score, delay }) => {
  const color = scoreColor(score);
  const pct = score ?? 0;

  return (
    <div
      className="flex items-center gap-3 px-3 py-2.5 rounded-xl transition-all hover:scale-[1.02]"
      style={{
        background: score !== null
          ? `linear-gradient(135deg, ${color}08 0%, ${color}03 100%)`
          : 'rgba(120,120,128,0.04)',
        border: `1px solid ${color}18`,
        animationDelay: `${delay}ms`,
      }}
    >
      <div
        className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
        style={{ background: `${color}14` }}
      >
        <svg className="w-4 h-4" style={{ color }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d={icon} />
        </svg>
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-xs font-medium truncate" style={{ color: 'var(--label-primary)' }}>{label}</p>
        <div className="flex items-center gap-2 mt-1">
          <div className="flex-1 h-1.5 rounded-full" style={{ background: `${color}15` }}>
            <div
              className="h-full rounded-full transition-all duration-1000"
              style={{
                width: `${pct}%`,
                background: color,
              }}
            />
          </div>
          <span className="text-xs font-semibold tabular-nums" style={{ color, minWidth: 28, textAlign: 'right' }}>
            {score !== null ? `${score}%` : '—'}
          </span>
        </div>
      </div>
    </div>
  );
};

// ─── Main Component ─────────────────────────────────────────────────────────

export const ComplianceHealthInfographic: React.FC<Props> = ({ sites }) => {
  const [selectedSiteId, setSelectedSiteId] = useState<string>(sites[0]?.site_id || '');
  const [data, setData] = useState<ComplianceHealthData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!selectedSiteId) return;
    setLoading(true);
    fetch(`/api/client/sites/${selectedSiteId}/compliance-health`, {
      credentials: 'include',
    })
      .then(r => r.ok ? r.json() : null)
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [selectedSiteId]);

  const categoryData = useMemo(() => {
    if (!data) return CATEGORIES.map(c => ({ ...c, score: null as number | null }));
    return CATEGORIES.map(c => ({
      ...c,
      score: data.breakdown[c.key] ?? null,
    }));
  }, [data]);

  if (sites.length === 0) return null;

  return (
    <div className="mb-8 rounded-2xl overflow-hidden" style={{
      background: 'var(--bg-secondary)',
      border: '1px solid var(--separator-light)',
      boxShadow: 'var(--card-shadow)',
    }}>
      {/* Header */}
      <div className="px-6 pt-6 pb-4 flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <div
            className="w-10 h-10 rounded-xl flex items-center justify-center"
            style={{
              background: 'linear-gradient(135deg, #14A89E 0%, #0E9189 100%)',
              boxShadow: '0 2px 10px rgba(20,168,158,0.25)',
            }}
          >
            <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
            </svg>
          </div>
          <div>
            <h2 className="text-lg font-semibold" style={{ color: 'var(--label-primary)' }}>
              Compliance Health
            </h2>
            <p className="text-sm" style={{ color: 'var(--label-secondary)' }}>
              Real-time HIPAA posture at a glance
            </p>
          </div>
        </div>

        {/* Site selector */}
        {sites.length > 1 && (
          <select
            value={selectedSiteId}
            onChange={e => setSelectedSiteId(e.target.value)}
            className="form-input text-sm py-1.5 px-3 w-auto min-w-[180px]"
          >
            {sites.map(s => (
              <option key={s.site_id} value={s.site_id}>{s.clinic_name}</option>
            ))}
          </select>
        )}
      </div>

      {/* Loading skeleton */}
      {loading && (
        <div className="px-6 pb-6">
          <div className="flex items-center justify-center py-12">
            <div className="w-[200px] h-[200px] rounded-full skeleton" />
          </div>
        </div>
      )}

      {/* Main infographic */}
      {!loading && data && (
        <div className="px-6 pb-6">
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">

            {/* Left: Gauge + Ring */}
            <div className="lg:col-span-4 flex flex-col items-center">
              <div className="relative" style={{ width: 260, height: 260 }}>
                {/* Outer category ring */}
                <CategoryRing
                  categories={categoryData}
                  size={260}
                />
                {/* Inner gauge */}
                <div className="absolute inset-0 flex items-center justify-center">
                  <CircularGauge score={data.overall_score} size={190} />
                </div>
              </div>

              {/* Legend */}
              <div className="flex flex-wrap justify-center gap-x-4 gap-y-1 mt-4">
                {[
                  { color: '#34C759', label: '90+' },
                  { color: '#14A89E', label: '75+' },
                  { color: '#FF9500', label: '50+' },
                  { color: '#FF3B30', label: '<50' },
                ].map(l => (
                  <div key={l.label} className="flex items-center gap-1.5">
                    <div className="w-2 h-2 rounded-full" style={{ background: l.color }} />
                    <span className="text-xs" style={{ color: 'var(--label-tertiary)' }}>{l.label}</span>
                  </div>
                ))}
              </div>

              {/* Counts summary */}
              <div className="flex items-center gap-4 mt-4 px-4 py-2.5 rounded-xl" style={{ background: 'var(--fill-quaternary)' }}>
                <div className="text-center">
                  <span className="text-sm font-bold tabular-nums text-green-600">{data.counts.passed}</span>
                  <p className="text-[10px]" style={{ color: 'var(--label-tertiary)' }}>Pass</p>
                </div>
                <div className="w-px h-6" style={{ background: 'var(--separator-light)' }} />
                <div className="text-center">
                  <span className="text-sm font-bold tabular-nums text-amber-500">{data.counts.warnings}</span>
                  <p className="text-[10px]" style={{ color: 'var(--label-tertiary)' }}>Warn</p>
                </div>
                <div className="w-px h-6" style={{ background: 'var(--separator-light)' }} />
                <div className="text-center">
                  <span className="text-sm font-bold tabular-nums text-red-500">{data.counts.failed}</span>
                  <p className="text-[10px]" style={{ color: 'var(--label-tertiary)' }}>Fail</p>
                </div>
              </div>
            </div>

            {/* Center: Category Breakdown */}
            <div className="lg:col-span-5">
              <p className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: 'var(--label-tertiary)' }}>
                Protection Categories
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 stagger-list">
                {categoryData.map((cat, i) => (
                  <CategoryCard
                    key={cat.key}
                    label={cat.label}
                    icon={cat.icon}
                    score={cat.score}
                    delay={i * 60}
                  />
                ))}
              </div>
            </div>

            {/* Right: Trend + Healing */}
            <div className="lg:col-span-3 flex flex-col gap-4">
              {/* 30-day trend */}
              <div className="p-4 rounded-xl" style={{ background: 'var(--fill-quaternary)' }}>
                <p className="text-xs font-semibold mb-2" style={{ color: 'var(--label-tertiary)' }}>
                  30-Day Trend
                </p>
                <Sparkline data={data.trend} width={200} height={52} />
                {data.trend.length >= 2 && (() => {
                  const first = data.trend[0].score;
                  const last = data.trend[data.trend.length - 1].score;
                  const diff = last - first;
                  const isUp = diff >= 0;
                  return (
                    <div className="flex items-center gap-1 mt-2">
                      <svg className="w-3.5 h-3.5" style={{ color: isUp ? '#34C759' : '#FF3B30' }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                          d={isUp ? 'M5 15l7-7 7 7' : 'M19 9l-7 7-7-7'} />
                      </svg>
                      <span className="text-xs font-semibold tabular-nums" style={{ color: isUp ? '#34C759' : '#FF3B30' }}>
                        {isUp ? '+' : ''}{diff.toFixed(1)}%
                      </span>
                      <span className="text-xs" style={{ color: 'var(--label-tertiary)' }}>this month</span>
                    </div>
                  );
                })()}
              </div>

              {/* Healing impact */}
              <div
                className="p-4 rounded-xl"
                style={{
                  background: data.healing.auto_healed > 0
                    ? 'linear-gradient(135deg, rgba(52,199,89,0.06) 0%, rgba(20,168,158,0.04) 100%)'
                    : 'var(--fill-quaternary)',
                  border: data.healing.auto_healed > 0
                    ? '1px solid rgba(52,199,89,0.15)'
                    : '1px solid transparent',
                }}
              >
                <p className="text-xs font-semibold mb-3" style={{ color: 'var(--label-tertiary)' }}>
                  Auto-Healing Impact
                </p>
                <div className="flex items-center gap-3">
                  <div
                    className="w-10 h-10 rounded-lg flex items-center justify-center"
                    style={{ background: 'rgba(52,199,89,0.12)' }}
                  >
                    <svg className="w-5 h-5 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                        d="M13 10V3L4 14h7v7l9-11h-7z" />
                    </svg>
                  </div>
                  <div>
                    <p className="text-xl font-bold tabular-nums" style={{ color: 'var(--label-primary)' }}>
                      {data.healing.auto_healed}
                    </p>
                    <p className="text-xs" style={{ color: 'var(--label-secondary)' }}>
                      issues auto-resolved
                    </p>
                  </div>
                </div>

                {data.healing.total > 0 && (
                  <div className="mt-3">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[10px]" style={{ color: 'var(--label-tertiary)' }}>
                        Auto-heal rate
                      </span>
                      <span className="text-xs font-semibold tabular-nums" style={{ color: '#34C759' }}>
                        {Math.round((data.healing.auto_healed / data.healing.total) * 100)}%
                      </span>
                    </div>
                    <div className="h-1.5 rounded-full" style={{ background: 'rgba(52,199,89,0.12)' }}>
                      <div
                        className="h-full rounded-full transition-all duration-1000"
                        style={{
                          width: `${(data.healing.auto_healed / data.healing.total) * 100}%`,
                          background: '#34C759',
                        }}
                      />
                    </div>
                  </div>
                )}

                {data.healing.pending > 0 && (
                  <div className="mt-2 flex items-center gap-1.5">
                    <div className="w-1.5 h-1.5 rounded-full bg-amber-400" />
                    <span className="text-xs" style={{ color: 'var(--label-tertiary)' }}>
                      {data.healing.pending} awaiting review
                    </span>
                  </div>
                )}
              </div>

              {/* Protected badge */}
              <div
                className="flex items-center gap-2 px-3 py-2 rounded-lg"
                style={{
                  background: data.overall_score !== null && data.overall_score >= 75
                    ? 'linear-gradient(135deg, rgba(20,168,158,0.08) 0%, rgba(20,168,158,0.03) 100%)'
                    : 'rgba(255,149,0,0.06)',
                  border: `1px solid ${data.overall_score !== null && data.overall_score >= 75 ? 'rgba(20,168,158,0.2)' : 'rgba(255,149,0,0.15)'}`,
                }}
              >
                <svg
                  className="w-4 h-4 flex-shrink-0"
                  style={{ color: data.overall_score !== null && data.overall_score >= 75 ? '#14A89E' : '#FF9500' }}
                  fill="none" stroke="currentColor" viewBox="0 0 24 24"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                </svg>
                <span className="text-xs font-medium" style={{
                  color: data.overall_score !== null && data.overall_score >= 75 ? '#14A89E' : '#FF9500',
                }}>
                  {data.overall_score !== null && data.overall_score >= 75
                    ? 'Protected by OsirisCare'
                    : 'Action recommended'}
                </span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* No data state */}
      {!loading && !data && (
        <div className="px-6 pb-6">
          <div className="flex flex-col items-center justify-center py-10">
            <svg className="w-12 h-12 mb-3" style={{ color: 'var(--label-tertiary)' }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
            </svg>
            <p className="text-sm font-medium" style={{ color: 'var(--label-secondary)' }}>
              Compliance data is being collected
            </p>
            <p className="text-xs mt-1" style={{ color: 'var(--label-tertiary)' }}>
              Results will appear after the first scan cycle
            </p>
          </div>
        </div>
      )}
    </div>
  );
};

export default ComplianceHealthInfographic;
