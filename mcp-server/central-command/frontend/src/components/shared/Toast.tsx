import React, { createContext, useContext, useState, useCallback, useRef } from 'react';

type ToastType = 'success' | 'error' | 'warning' | 'info';

interface Toast {
  id: string;
  type: ToastType;
  message: string;
  duration?: number;
}

interface ToastContextValue {
  addToast: (type: ToastType, message: string, duration?: number) => void;
  removeToast: (id: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within ToastProvider');
  return ctx;
}

const ICONS: Record<ToastType, React.ReactNode> = {
  success: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path d="M8 1.5a6.5 6.5 0 100 13 6.5 6.5 0 000-13z" fill="#34C759" fillOpacity="0.15"/>
      <path d="M5.5 8l2 2 3.5-4" stroke="#34C759" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
  error: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path d="M8 1.5a6.5 6.5 0 100 13 6.5 6.5 0 000-13z" fill="#FF3B30" fillOpacity="0.15"/>
      <path d="M6 6l4 4M10 6l-4 4" stroke="#FF3B30" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  ),
  warning: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path d="M8 1.5a6.5 6.5 0 100 13 6.5 6.5 0 000-13z" fill="#FF9500" fillOpacity="0.15"/>
      <path d="M8 5v3M8 10.5h.01" stroke="#FF9500" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  ),
  info: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path d="M8 1.5a6.5 6.5 0 100 13 6.5 6.5 0 000-13z" fill="#14A89E" fillOpacity="0.15"/>
      <path d="M8 7v4M8 5h.01" stroke="#14A89E" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  ),
};

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const counterRef = useRef(0);

  const removeToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const addToast = useCallback((type: ToastType, message: string, duration = 5000) => {
    const id = `toast-${++counterRef.current}`;
    setToasts(prev => [...prev, { id, type, message, duration }]);
    if (duration > 0) {
      setTimeout(() => removeToast(id), duration);
    }
  }, [removeToast]);

  return (
    <ToastContext.Provider value={{ addToast, removeToast }}>
      {children}
      <div className="toast-container">
        {toasts.map(toast => (
          <div key={toast.id} className={`toast toast-${toast.type}`}>
            <div className="flex items-center gap-2">
              {ICONS[toast.type]}
              <span className="text-label-primary">{toast.message}</span>
              <button
                onClick={() => removeToast(toast.id)}
                className="ml-auto text-label-tertiary hover:text-label-primary transition-colors"
                aria-label="Dismiss"
              >
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  <path d="M4 4l6 6M10 4l-6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
                </svg>
              </button>
            </div>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
};
