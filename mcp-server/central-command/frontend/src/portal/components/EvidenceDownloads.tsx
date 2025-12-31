import React, { useState } from 'react';

interface EvidenceBundle {
  bundle_id: string;
  bundle_type: string;
  generated_at: string;
  size_bytes: number;
}

interface EvidenceDownloadsProps {
  bundles: EvidenceBundle[];
  siteId: string;
  token: string;
}

export const EvidenceDownloads: React.FC<EvidenceDownloadsProps> = ({ bundles, siteId, token }) => {
  const [downloading, setDownloading] = useState<string | null>(null);

  const formatSize = (bytes: number) => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
  };

  const formatType = (type: string) => {
    const icons: Record<string, string> = {
      daily: '📄',
      weekly: '📊',
      monthly: '📑'
    };
    return {
      icon: icons[type] || '📁',
      label: type.charAt(0).toUpperCase() + type.slice(1)
    };
  };

  const handleDownload = async (bundleId: string) => {
    setDownloading(bundleId);
    try {
      const response = await fetch(
        `/api/portal/site/${siteId}/evidence/${bundleId}/download?token=${token}`
      );
      const data = await response.json();
      if (data.download_url) {
        window.open(data.download_url, '_blank');
      }
    } catch (error) {
      console.error('Download failed:', error);
    } finally {
      setDownloading(null);
    }
  };

  if (bundles.length === 0) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 p-8 text-center">
        <div className="text-4xl mb-3">📁</div>
        <p className="text-gray-600">No evidence bundles yet</p>
        <p className="text-sm text-gray-400 mt-1">Bundles will appear after first compliance check</p>
      </div>
    );
  }

  // Group by type
  const grouped = bundles.reduce((acc, bundle) => {
    const type = bundle.bundle_type;
    if (!acc[type]) acc[type] = [];
    acc[type].push(bundle);
    return acc;
  }, {} as Record<string, EvidenceBundle[]>);

  return (
    <div className="space-y-4">
      {Object.entries(grouped).map(([type, typeBundles]) => {
        const { icon, label } = formatType(type);
        return (
          <div key={type} className="bg-white rounded-xl border border-gray-200">
            <div className="px-4 py-3 border-b border-gray-100 flex items-center gap-2">
              <span>{icon}</span>
              <span className="font-medium text-gray-900">{label} Reports</span>
              <span className="text-xs text-gray-400">({typeBundles.length})</span>
            </div>
            <div className="divide-y divide-gray-100">
              {typeBundles.slice(0, 5).map((bundle) => (
                <div
                  key={bundle.bundle_id}
                  className="px-4 py-3 flex items-center justify-between hover:bg-gray-50"
                >
                  <div>
                    <p className="text-sm font-medium text-gray-900">{bundle.bundle_id}</p>
                    <p className="text-xs text-gray-500">
                      {new Date(bundle.generated_at).toLocaleDateString()} • {formatSize(bundle.size_bytes)}
                    </p>
                  </div>
                  <button
                    onClick={() => handleDownload(bundle.bundle_id)}
                    disabled={downloading === bundle.bundle_id}
                    className="px-3 py-1.5 text-sm font-medium text-blue-600 hover:text-blue-700 hover:bg-blue-50 rounded-lg disabled:opacity-50 disabled:cursor-wait"
                  >
                    {downloading === bundle.bundle_id ? 'Loading...' : 'Download'}
                  </button>
                </div>
              ))}
            </div>
          </div>
        );
      })}

      {/* Monthly Report Section */}
      <div className="bg-gradient-to-r from-blue-50 to-indigo-50 rounded-xl border border-blue-200 p-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="font-semibold text-gray-900">Monthly Compliance Packet</h3>
            <p className="text-sm text-gray-600 mt-1">
              PDF report with full compliance summary, incident log, and evidence index
            </p>
          </div>
          <a
            href={`/api/portal/site/${siteId}/report/monthly?token=${token}`}
            target="_blank"
            rel="noopener noreferrer"
            className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 whitespace-nowrap"
          >
            Download PDF
          </a>
        </div>
      </div>
    </div>
  );
};
