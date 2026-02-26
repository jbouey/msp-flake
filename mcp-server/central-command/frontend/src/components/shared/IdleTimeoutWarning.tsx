import React from 'react';

interface IdleTimeoutWarningProps {
  remainingSeconds: number;
  onDismiss: () => void;
}

export const IdleTimeoutWarning: React.FC<IdleTimeoutWarningProps> = ({
  remainingSeconds,
  onDismiss,
}) => {
  const minutes = Math.floor(remainingSeconds / 60);
  const seconds = remainingSeconds % 60;
  const timeStr = minutes > 0 ? `${minutes}:${seconds.toString().padStart(2, '0')}` : `${seconds}s`;

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-lg shadow-xl p-6 max-w-sm mx-4">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-full bg-amber-100 flex items-center justify-center">
            <svg className="w-5 h-5 text-amber-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
            </svg>
          </div>
          <div>
            <h3 className="text-lg font-semibold text-slate-900">Session Expiring</h3>
            <p className="text-sm text-slate-500">Due to inactivity</p>
          </div>
        </div>
        <p className="text-slate-700 mb-4">
          Your session will expire in <span className="font-mono font-bold text-amber-600">{timeStr}</span> due to inactivity. Click below to stay signed in.
        </p>
        <button
          onClick={onDismiss}
          className="w-full px-4 py-2 bg-indigo-600 text-white rounded-md hover:bg-indigo-700 transition-colors font-medium"
        >
          Stay Signed In
        </button>
      </div>
    </div>
  );
};
