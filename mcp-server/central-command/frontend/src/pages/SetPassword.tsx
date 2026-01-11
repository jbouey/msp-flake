import React, { useState, useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { GlassCard, Spinner } from '../components/shared';
import { usersApi, InviteValidation } from '../utils/api';

/**
 * Set Password Page - Public page for accepting user invites
 */
export default function SetPassword() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token');

  const [validating, setValidating] = useState(true);
  const [validation, setValidation] = useState<InviteValidation | null>(null);
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    if (!token) {
      setValidating(false);
      setValidation({ valid: false, error: 'No invite token provided' });
      return;
    }

    usersApi.validateInvite(token)
      .then(result => {
        setValidation(result);
      })
      .catch(() => {
        setValidation({ valid: false, error: 'Failed to validate invite' });
      })
      .finally(() => {
        setValidating(false);
      });
  }, [token]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token || !validation?.valid) return;

    if (password !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }

    if (password.length < 8) {
      setError('Password must be at least 8 characters');
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      await usersApi.acceptInvite({
        token,
        password,
        confirm_password: confirmPassword,
      });
      setSuccess(true);
      // Redirect to login after 3 seconds
      setTimeout(() => {
        navigate('/');
      }, 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to set password');
    } finally {
      setSubmitting(false);
    }
  };

  const passwordsMatch = password === confirmPassword;
  const passwordValid = password.length >= 8;

  // Loading state
  if (validating) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 via-purple-900 to-slate-900 flex items-center justify-center">
        <GlassCard className="w-full max-w-md text-center py-12">
          <Spinner />
          <p className="mt-4 text-label-secondary">Validating invite...</p>
        </GlassCard>
      </div>
    );
  }

  // Invalid invite
  if (!validation?.valid) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 via-purple-900 to-slate-900 flex items-center justify-center p-4">
        <GlassCard className="w-full max-w-md text-center py-12">
          <div className="w-16 h-16 mx-auto mb-6 rounded-full bg-red-500/20 flex items-center justify-center">
            <svg className="w-8 h-8 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </div>
          <h1 className="text-2xl font-bold text-label-primary mb-2">Invalid Invite</h1>
          <p className="text-label-tertiary mb-6">{validation?.error || 'This invite link is invalid or has expired.'}</p>
          <button
            onClick={() => navigate('/')}
            className="px-6 py-2 bg-accent-primary text-white rounded-lg hover:bg-accent-primary/90 transition-colors"
          >
            Go to Login
          </button>
        </GlassCard>
      </div>
    );
  }

  // Success state
  if (success) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 via-purple-900 to-slate-900 flex items-center justify-center p-4">
        <GlassCard className="w-full max-w-md text-center py-12">
          <div className="w-16 h-16 mx-auto mb-6 rounded-full bg-green-500/20 flex items-center justify-center">
            <svg className="w-8 h-8 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <h1 className="text-2xl font-bold text-label-primary mb-2">Account Created!</h1>
          <p className="text-label-tertiary mb-6">Your password has been set. Redirecting to login...</p>
          <Spinner />
        </GlassCard>
      </div>
    );
  }

  // Set password form
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-purple-900 to-slate-900 flex items-center justify-center p-4">
      <GlassCard className="w-full max-w-md">
        <div className="text-center mb-8">
          <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-accent-primary/20 flex items-center justify-center">
            <svg className="w-8 h-8 text-accent-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
            </svg>
          </div>
          <h1 className="text-2xl font-bold text-label-primary">Set Your Password</h1>
          <p className="text-label-tertiary mt-2">
            Welcome to Central Command! Set your password to activate your account.
          </p>
        </div>

        {/* Invite info */}
        <div className="bg-fill-secondary rounded-lg p-4 mb-6">
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <span className="text-label-tertiary">Email:</span>
              <p className="text-label-primary font-medium">{validation.email}</p>
            </div>
            <div>
              <span className="text-label-tertiary">Role:</span>
              <p className="text-label-primary font-medium capitalize">{validation.role}</p>
            </div>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-4 py-3 bg-fill-secondary border border-separator-default rounded-lg text-label-primary focus:outline-none focus:border-accent-primary"
              placeholder="Minimum 8 characters"
              required
            />
            {password && !passwordValid && (
              <p className="text-xs text-red-400 mt-1">Password must be at least 8 characters</p>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">
              Confirm Password
            </label>
            <input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="w-full px-4 py-3 bg-fill-secondary border border-separator-default rounded-lg text-label-primary focus:outline-none focus:border-accent-primary"
              placeholder="Re-enter your password"
              required
            />
            {confirmPassword && !passwordsMatch && (
              <p className="text-xs text-red-400 mt-1">Passwords do not match</p>
            )}
          </div>

          {error && (
            <div className="p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting || !passwordValid || !passwordsMatch}
            className="w-full py-3 bg-accent-primary text-white rounded-lg font-medium hover:bg-accent-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting ? <Spinner size="sm" /> : 'Create Account'}
          </button>
        </form>

        <p className="text-center text-label-tertiary text-sm mt-6">
          Already have an account?{' '}
          <button
            onClick={() => navigate('/')}
            className="text-accent-primary hover:underline"
          >
            Sign in
          </button>
        </p>
      </GlassCard>
    </div>
  );
}
