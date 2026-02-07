import React, { useState, useCallback } from 'react';
import { GlassCard, Spinner } from '../components/shared';
import { PatternCard, PromotionTimeline } from '../components/learning';
import {
  useLearningStatus,
  usePromotionCandidates,
  usePromotionHistory,
  usePromotePattern,
  useRejectPattern,
} from '../hooks';

/**
 * Learning page - L2 -> L1 promotion dashboard
 *
 * The Learning Loop is the data flywheel that promotes successful
 * L2 (LLM-assisted) resolutions to L1 (deterministic) rules.
 */
export const Learning: React.FC = () => {
  const [promotingId, setPromotingId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  // Fetch data
  const { data: status, isLoading: isLoadingStatus, isError: isStatusError } = useLearningStatus();
  const { data: candidates = [], isLoading: isLoadingCandidates, isError: isCandidatesError } = usePromotionCandidates();
  const { data: history = [], isLoading: isLoadingHistory } = usePromotionHistory(50);

  // Mutations
  const promoteMutation = usePromotePattern();
  const rejectMutation = useRejectPattern();

  const showFeedback = useCallback((type: 'success' | 'error', message: string) => {
    setFeedback({ type, message });
    setTimeout(() => setFeedback(null), 4000);
  }, []);

  const handleApprove = useCallback(async (patternId: string) => {
    setPromotingId(patternId);
    try {
      const result = await promoteMutation.mutateAsync(patternId);
      showFeedback('success', `Pattern promoted to ${result.new_rule_id}`);
    } catch (error) {
      showFeedback('error', `Failed to promote: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      setPromotingId(null);
    }
  }, [promoteMutation, showFeedback]);

  const handleReject = useCallback(async (patternId: string) => {
    try {
      await rejectMutation.mutateAsync(patternId);
      showFeedback('success', 'Pattern rejected');
    } catch (error) {
      showFeedback('error', `Failed to reject: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  }, [rejectMutation, showFeedback]);

  const handleApproveAll = async () => {
    for (const candidate of candidates) {
      await handleApprove(candidate.id);
    }
  };

  return (
    <div className="space-y-6">
      {/* Feedback banner */}
      {feedback && (
        <div className={`px-4 py-3 rounded-lg text-sm font-medium ${
          feedback.type === 'success'
            ? 'bg-health-healthy/10 text-health-healthy border border-health-healthy/20'
            : 'bg-health-critical/10 text-health-critical border border-health-critical/20'
        }`}>
          {feedback.message}
        </div>
      )}

      {/* Error state */}
      {(isStatusError || isCandidatesError) && (
        <div className="bg-health-critical/10 border border-health-critical/20 px-4 py-3 rounded-lg">
          <p className="text-sm text-health-critical font-medium">
            Failed to load learning data. Check your connection and try refreshing.
          </p>
        </div>
      )}

      {/* Stats row */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <GlassCard padding="md">
          <p className="text-label-tertiary text-sm">L1 Rules</p>
          {isLoadingStatus ? (
            <div className="h-9 animate-pulse bg-separator-light rounded mt-1" />
          ) : (
            <>
              <p className="text-3xl font-semibold">{status?.total_l1_rules ?? 0}</p>
              <p className="text-xs text-health-healthy">Deterministic resolutions</p>
            </>
          )}
        </GlassCard>

        <GlassCard padding="md">
          <p className="text-label-tertiary text-sm">L2 Decisions (30d)</p>
          {isLoadingStatus ? (
            <div className="h-9 animate-pulse bg-separator-light rounded mt-1" />
          ) : (
            <>
              <p className="text-3xl font-semibold">{status?.total_l2_decisions_30d ?? 0}</p>
              <p className="text-xs text-label-tertiary">LLM-assisted resolutions</p>
            </>
          )}
        </GlassCard>

        <GlassCard padding="md">
          <p className="text-label-tertiary text-sm">L1 Resolution Rate</p>
          {isLoadingStatus ? (
            <div className="h-9 animate-pulse bg-separator-light rounded mt-1" />
          ) : (
            <>
              <p className="text-3xl font-semibold text-health-healthy">
                {status?.l1_resolution_rate?.toFixed(1) ?? 0}%
              </p>
              <p className="text-xs text-label-tertiary">Target: 70-80%</p>
            </>
          )}
        </GlassCard>

        <GlassCard padding="md">
          <p className="text-label-tertiary text-sm">Promotion Success</p>
          {isLoadingStatus ? (
            <div className="h-9 animate-pulse bg-separator-light rounded mt-1" />
          ) : (
            <>
              <p className={`text-3xl font-semibold ${
                (status?.promotion_success_rate ?? 0) >= 90
                  ? 'text-health-healthy'
                  : 'text-health-warning'
              }`}>
                {status?.promotion_success_rate?.toFixed(1) ?? 0}%
              </p>
              <p className="text-xs text-label-tertiary">Post-promotion accuracy</p>
            </>
          )}
        </GlassCard>
      </div>

      {/* Awaiting promotion */}
      <GlassCard>
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-lg font-semibold">
              Awaiting Promotion ({candidates.length})
            </h2>
            <p className="text-sm text-label-tertiary mt-1">
              L2 patterns with consistent success ready for L1 promotion
            </p>
          </div>
          {candidates.length > 0 && (
            <button
              onClick={handleApproveAll}
              disabled={promotingId !== null}
              className="btn-primary text-sm disabled:opacity-50"
            >
              Approve All
            </button>
          )}
        </div>

        {isLoadingCandidates ? (
          <div className="flex items-center justify-center py-12">
            <Spinner size="lg" />
          </div>
        ) : candidates.length === 0 ? (
          <div className="text-center py-12">
            <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-health-healthy/10 flex items-center justify-center">
              <svg className="w-8 h-8 text-health-healthy" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <p className="text-label-secondary font-medium">All caught up!</p>
            <p className="text-label-tertiary text-sm mt-1">
              No patterns awaiting promotion at this time.
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            {candidates.map((candidate) => (
              <PatternCard
                key={candidate.id}
                candidate={candidate}
                onApprove={handleApprove}
                onReject={handleReject}
                isPromoting={promotingId === candidate.id}
              />
            ))}
          </div>
        )}
      </GlassCard>

      {/* Recently promoted */}
      <PromotionTimeline history={history} isLoading={isLoadingHistory} />

      {/* Info section */}
      <GlassCard padding="md">
        <h3 className="font-semibold text-label-primary mb-2">How the Learning Loop Works</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm text-label-secondary">
          <div className="flex gap-3">
            <div className="w-8 h-8 flex-shrink-0 rounded-full bg-level-l2/20 flex items-center justify-center">
              <span className="text-level-l2 font-bold text-xs">L2</span>
            </div>
            <div>
              <p className="font-medium text-label-primary">Pattern Detection</p>
              <p className="text-xs">LLM identifies recurring incident patterns and successful resolutions</p>
            </div>
          </div>
          <div className="flex gap-3">
            <div className="w-8 h-8 flex-shrink-0 rounded-full bg-accent-primary/20 flex items-center justify-center">
              <svg className="w-4 h-4 text-accent-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <div>
              <p className="font-medium text-label-primary">Human Review</p>
              <p className="text-xs">Admin approves patterns meeting success thresholds for promotion</p>
            </div>
          </div>
          <div className="flex gap-3">
            <div className="w-8 h-8 flex-shrink-0 rounded-full bg-level-l1/20 flex items-center justify-center">
              <span className="text-level-l1 font-bold text-xs">L1</span>
            </div>
            <div>
              <p className="font-medium text-label-primary">Deterministic Rule</p>
              <p className="text-xs">Pattern becomes instant L1 resolution - no LLM cost, &lt;100ms</p>
            </div>
          </div>
        </div>
      </GlassCard>
    </div>
  );
};

export default Learning;
