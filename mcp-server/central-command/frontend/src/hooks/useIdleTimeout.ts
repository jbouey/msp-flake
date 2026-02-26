import { useEffect, useRef, useCallback, useState } from 'react';

const IDLE_TIMEOUT_MS = 15 * 60 * 1000; // 15 minutes — matches server-side
const WARNING_BEFORE_MS = 2 * 60 * 1000; // Show warning 2 min before logout

interface UseIdleTimeoutOptions {
  onTimeout: () => void;
  enabled?: boolean;
}

/**
 * HIPAA §164.312(a)(2)(iii) — Automatic logoff after inactivity.
 * Tracks mouse, keyboard, scroll, and touch events.
 * Shows a warning 2 minutes before auto-logout.
 */
export function useIdleTimeout({ onTimeout, enabled = true }: UseIdleTimeoutOptions) {
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const warningRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [showWarning, setShowWarning] = useState(false);
  const [remainingSeconds, setRemainingSeconds] = useState(0);
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const clearTimers = useCallback(() => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    if (warningRef.current) clearTimeout(warningRef.current);
    if (countdownRef.current) clearInterval(countdownRef.current);
    setShowWarning(false);
  }, []);

  const resetTimer = useCallback(() => {
    if (!enabled) return;
    clearTimers();

    // Set warning timer (fires 2 min before timeout)
    warningRef.current = setTimeout(() => {
      setShowWarning(true);
      setRemainingSeconds(Math.floor(WARNING_BEFORE_MS / 1000));
      countdownRef.current = setInterval(() => {
        setRemainingSeconds(prev => {
          if (prev <= 1) {
            if (countdownRef.current) clearInterval(countdownRef.current);
            return 0;
          }
          return prev - 1;
        });
      }, 1000);
    }, IDLE_TIMEOUT_MS - WARNING_BEFORE_MS);

    // Set logout timer
    timeoutRef.current = setTimeout(() => {
      setShowWarning(false);
      onTimeout();
    }, IDLE_TIMEOUT_MS);
  }, [enabled, onTimeout, clearTimers]);

  useEffect(() => {
    if (!enabled) return;

    const events = ['mousedown', 'keydown', 'scroll', 'touchstart', 'mousemove'];

    const handleActivity = () => {
      resetTimer();
    };

    events.forEach(event => document.addEventListener(event, handleActivity, { passive: true }));
    resetTimer();

    return () => {
      events.forEach(event => document.removeEventListener(event, handleActivity));
      clearTimers();
    };
  }, [enabled, resetTimer, clearTimers]);

  const dismissWarning = useCallback(() => {
    resetTimer(); // User activity — reset everything
  }, [resetTimer]);

  return { showWarning, remainingSeconds, dismissWarning };
}
