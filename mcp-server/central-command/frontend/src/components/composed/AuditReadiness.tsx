import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { GlassCard, Button, Spinner } from '../shared';
import { OPS_STATUS_CONFIG } from '../../constants/status';
import { AUDIT_BADGE_LABELS } from '../../constants/copy';
import type { OpsStatus } from '../../constants/status';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AuditCheck {
  name: string;
  passed: boolean;
  detail: string;
}

interface AuditCountdown {
  next_audit_date: string;
  days_remaining: number;
  urgency: string;
}

interface EvidenceStats {
  total_bundles: number;
  signed: number;
  signing_rate: number;
}

interface AuditReadinessData {
  org_id: string;
  org_name: string;
  badge: OpsStatus;
  ready: boolean;
  checks: AuditCheck[];
  blockers: string[];
  passed_count: number;
  total_checks: number;
  countdown: AuditCountdown | null;
  evidence_stats: EvidenceStats;
}

interface AuditReadinessProps {
  orgId: string;
}

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------

const CheckIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} viewBox="0 0 20 20" fill="currentColor">
    <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
  </svg>
);

const XIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} viewBox="0 0 20 20" fill="currentColor">
    <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
  </svg>
);

const CalendarIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 012.25-2.25h13.5A2.25 2.25 0 0121 7.5v11.25m-18 0A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75m-18 0v-7.5A2.25 2.25 0 015.25 9h13.5A2.25 2.25 0 0121 11.25v7.5" />
  </svg>
);

const DocumentIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
  </svg>
);

// ---------------------------------------------------------------------------
// Countdown display
// ---------------------------------------------------------------------------

function CountdownDisplay({ countdown }: { countdown: AuditCountdown }) {
  const { days_remaining, urgency, next_audit_date } = countdown;

  const urgencyClasses: Record<string, string> = {
    normal: 'text-label-secondary',
    urgent: 'text-amber-500 dark:text-amber-400',
    critical: 'text-red-500 dark:text-red-400',
    overdue: 'text-red-500 dark:text-red-400 font-bold',
  };

  const colorClass = urgencyClasses[urgency] || urgencyClasses.normal;
  const formattedDate = new Date(next_audit_date).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });

  let label: string;
  if (urgency === 'overdue') {
    label = `${Math.abs(days_remaining)} days overdue`;
  } else if (days_remaining === 0) {
    label = 'Audit is today';
  } else if (days_remaining === 1) {
    label = '1 day until next audit';
  } else {
    label = `${days_remaining} days until next audit`;
  }

  return (
    <div className="flex items-center gap-2">
      <CalendarIcon className="w-4 h-4 text-label-tertiary flex-shrink-0" />
      <div>
        <span className={`text-sm font-medium ${colorClass}`}>{label}</span>
        <span className="text-xs text-label-tertiary ml-2">({formattedDate})</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Badge display
// ---------------------------------------------------------------------------

function AuditBadge({ badge }: { badge: OpsStatus }) {
  const config = OPS_STATUS_CONFIG[badge];
  const label = AUDIT_BADGE_LABELS[badge] || 'Unknown';

  return (
    <div className="flex items-center gap-2.5">
      <div className="relative">
        <div className={`w-3 h-3 rounded-full ${config.bgColor} ${config.ringColor} ring-4`} />
        {badge === 'green' && (
          <div className={`absolute inset-0 w-3 h-3 rounded-full ${config.pulseColor} animate-ping`} />
        )}
      </div>
      <span className={`text-sm font-semibold ${config.color}`}>{label}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Checklist
// ---------------------------------------------------------------------------

function AuditChecklist({ checks }: { checks: AuditCheck[] }) {
  return (
    <div className="space-y-2">
      {checks.map((check) => (
        <div key={check.name} className="flex items-start gap-2.5">
          {check.passed ? (
            <CheckIcon className="w-4.5 h-4.5 text-emerald-500 dark:text-emerald-400 flex-shrink-0 mt-0.5" />
          ) : (
            <XIcon className="w-4.5 h-4.5 text-red-500 dark:text-red-400 flex-shrink-0 mt-0.5" />
          )}
          <div className="min-w-0">
            <span className="text-sm text-label-primary">{check.name}</span>
            <p className="text-xs text-label-tertiary">{check.detail}</p>
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Blockers
// ---------------------------------------------------------------------------

function BlockersList({ blockers }: { blockers: string[] }) {
  if (blockers.length === 0) return null;

  return (
    <div className="space-y-2">
      <h4 className="text-xs font-semibold text-label-secondary uppercase tracking-wide">
        Blockers
      </h4>
      <ul className="space-y-1.5">
        {blockers.map((blocker, i) => (
          <li key={i} className="flex items-start gap-2 text-sm text-red-500 dark:text-red-400">
            <span className="flex-shrink-0 mt-1 w-1.5 h-1.5 rounded-full bg-red-500 dark:bg-red-400" />
            {blocker}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Actions panel with modals
// ---------------------------------------------------------------------------

function AuditActions({
  orgId,
  data,
}: {
  orgId: string;
  data: AuditReadinessData;
}) {
  const queryClient = useQueryClient();
  const [showDatePicker, setShowDatePicker] = useState(false);
  const [auditDate, setAuditDate] = useState(data.countdown?.next_audit_date?.split('T')[0] || '');

  const mutation = useMutation({
    mutationFn: async (body: Record<string, unknown>) => {
      const csrfToken = document.cookie.match(/csrf_token=([^;]+)/)?.[1] || '';
      const res = await fetch(`/api/dashboard/ops/audit-config/${orgId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
        credentials: 'same-origin',
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error('Failed to update');
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['audit-readiness', orgId] });
      setShowDatePicker(false);
    },
  });

  const handleToggleBaa = () => {
    const currentBaa = data.checks.find(c => c.name.toLowerCase().includes('baa'));
    mutation.mutate({ baa_on_file: !currentBaa?.passed });
  };

  const handleSetDate = () => {
    if (auditDate) {
      mutation.mutate({ next_audit_date: auditDate });
    }
  };

  const handleGenerateReport = () => {
    window.open(`/api/dashboard/ops/audit-report/${orgId}`, '_blank');
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="primary"
          icon={<DocumentIcon className="w-3.5 h-3.5" />}
          onClick={handleGenerateReport}
        >
          Generate Audit Report
        </Button>
        <Button
          size="sm"
          variant="secondary"
          icon={<CalendarIcon className="w-3.5 h-3.5" />}
          onClick={() => setShowDatePicker(!showDatePicker)}
        >
          Set Audit Date
        </Button>
        <Button
          size="sm"
          variant="ghost"
          loading={mutation.isPending}
          onClick={handleToggleBaa}
        >
          Toggle BAA
        </Button>
      </div>

      {showDatePicker && (
        <div className="flex items-center gap-2 p-3 rounded-ios-md bg-fill-secondary">
          <input
            type="date"
            value={auditDate}
            onChange={(e) => setAuditDate(e.target.value)}
            className="px-3 py-1.5 text-sm rounded-ios-sm border border-separator-light bg-background-primary text-label-primary focus:outline-none focus:ring-2 focus:ring-accent-primary"
          />
          <Button size="sm" variant="primary" onClick={handleSetDate} loading={mutation.isPending}>
            Save
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setShowDatePicker(false)}>
            Cancel
          </Button>
        </div>
      )}

      {mutation.isError && (
        <p className="text-xs text-red-500">
          {mutation.error instanceof Error ? mutation.error.message : 'Update failed'}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function AuditReadiness({ orgId }: AuditReadinessProps) {
  const { data, isLoading, error } = useQuery<AuditReadinessData>({
    queryKey: ['audit-readiness', orgId],
    queryFn: async () => {
      const res = await fetch(`/api/dashboard/ops/audit-readiness/${orgId}`, {
        credentials: 'same-origin',
      });
      if (!res.ok) throw new Error('Failed to fetch audit readiness');
      return res.json();
    },
    refetchInterval: 60_000,
  });

  if (isLoading) {
    return (
      <GlassCard>
        <div className="flex items-center justify-center py-8">
          <Spinner size="md" />
        </div>
      </GlassCard>
    );
  }

  if (error || !data) {
    return (
      <GlassCard>
        <p className="text-sm text-health-critical">
          {error instanceof Error ? error.message : 'Failed to load audit readiness'}
        </p>
      </GlassCard>
    );
  }

  return (
    <GlassCard>
      <div className="space-y-5">
        {/* Header: org name + badge + score */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div className="space-y-1">
            <h3 className="text-base font-semibold text-label-primary">{data.org_name}</h3>
            <AuditBadge badge={data.badge} />
          </div>
          <div className="text-right">
            <div className="text-2xl font-bold text-label-primary">
              {data.passed_count}/{data.total_checks}
            </div>
            <div className="text-xs text-label-tertiary">checks passing</div>
          </div>
        </div>

        {/* Countdown */}
        {data.countdown && (
          <CountdownDisplay countdown={data.countdown} />
        )}

        {/* Evidence stats summary */}
        <div className="grid grid-cols-3 gap-4 py-3 border-y border-separator-light">
          <div>
            <div className="text-xs text-label-tertiary">Evidence Bundles</div>
            <div className="text-sm font-semibold text-label-primary">
              {data.evidence_stats.total_bundles.toLocaleString()}
            </div>
          </div>
          <div>
            <div className="text-xs text-label-tertiary">Signed</div>
            <div className="text-sm font-semibold text-label-primary">
              {data.evidence_stats.signed.toLocaleString()}
            </div>
          </div>
          <div>
            <div className="text-xs text-label-tertiary">Signing Rate</div>
            <div className="text-sm font-semibold text-label-primary">
              {data.evidence_stats.signing_rate.toFixed(1)}%
            </div>
          </div>
        </div>

        {/* Checklist */}
        <AuditChecklist checks={data.checks} />

        {/* Blockers (yellow/red badge only) */}
        {data.badge !== 'green' && data.blockers.length > 0 && (
          <BlockersList blockers={data.blockers} />
        )}

        {/* Actions */}
        <div className="pt-2 border-t border-separator-light">
          <AuditActions orgId={orgId} data={data} />
        </div>
      </div>
    </GlassCard>
  );
}

export default AuditReadiness;
