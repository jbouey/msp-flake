import { OPS_STATUS_CONFIG, type OpsStatus } from '../../constants/status';

interface StatusLightProps {
  status: OpsStatus;
  title: string;
  label: string;
  tooltip?: string;
  docsAnchor?: string;
  stats?: Record<string, string | number>;
  onClick?: () => void;
  expanded?: boolean;
}

export function StatusLight({ status, title, label, tooltip, docsAnchor, stats, onClick, expanded }: StatusLightProps) {
  const config = OPS_STATUS_CONFIG[status];

  return (
    <button
      onClick={onClick}
      className={`
        relative flex flex-col items-center gap-2 p-4 rounded-xl border transition-all
        ${expanded ? 'ring-2 ' + config.ringColor + ' border-white/20' : 'border-white/10 hover:border-white/20'}
        bg-white/5 backdrop-blur-sm cursor-pointer w-full text-left
      `}
      title={tooltip}
    >
      {/* Traffic light dot with pulse on red */}
      <div className="relative">
        <div className={`w-4 h-4 rounded-full ${config.bgColor}`} />
        {status === 'red' && (
          <div className={`absolute inset-0 w-4 h-4 rounded-full ${config.pulseColor} animate-ping`} />
        )}
      </div>
      <div className="text-sm font-semibold text-label-primary">{title}</div>
      <div className={`text-xs font-medium ${config.color}`}>{label}</div>

      {stats && (
        <div className="text-xs text-label-tertiary mt-1 text-center space-y-0.5">
          {Object.entries(stats).map(([k, v]) => (
            <div key={k}>{k}: <span className="text-label-secondary font-medium">{v}</span></div>
          ))}
        </div>
      )}

      {docsAnchor && (
        <a
          href={`/docs${docsAnchor}`}
          onClick={e => e.stopPropagation()}
          className="absolute top-2 right-2 text-label-tertiary hover:text-label-secondary text-xs"
          title="What does this mean?"
        >
          (?)
        </a>
      )}
    </button>
  );
}
