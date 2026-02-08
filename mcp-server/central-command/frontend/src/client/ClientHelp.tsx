import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useClient } from './ClientContext';

interface HelpSection {
  id: string;
  title: string;
  icon: string;
  content: React.ReactNode;
}

// Visual diagram component for evidence chain
const EvidenceChainDiagram: React.FC = () => (
  <div className="my-6 p-6 bg-gradient-to-r from-slate-50 to-teal-50 rounded-xl border border-teal-200">
    <h5 className="font-medium text-gray-900 mb-4 text-center">How Evidence Chain Works</h5>

    {/* Visual chain representation */}
    <div className="flex items-center justify-center gap-2 overflow-x-auto py-4">
      {/* Block 1 */}
      <div className="flex-shrink-0 w-32">
        <div className="bg-white rounded-lg border-2 border-teal-500 p-3 shadow-sm">
          <div className="text-xs text-gray-500 mb-1">Check #1</div>
          <div className="text-sm font-medium text-gray-900">Firewall</div>
          <div className="mt-2 font-mono text-xs bg-gray-100 p-1 rounded truncate">
            hash: a3f2...
          </div>
        </div>
        <div className="text-center text-xs text-gray-500 mt-1">9:00 AM</div>
      </div>

      {/* Arrow */}
      <div className="flex-shrink-0 flex items-center">
        <div className="w-8 h-0.5 bg-teal-400"></div>
        <svg className="w-4 h-4 text-teal-400 -ml-1" fill="currentColor" viewBox="0 0 20 20">
          <path fillRule="evenodd" d="M10.293 3.293a1 1 0 011.414 0l6 6a1 1 0 010 1.414l-6 6a1 1 0 01-1.414-1.414L14.586 11H3a1 1 0 110-2h11.586l-4.293-4.293a1 1 0 010-1.414z" clipRule="evenodd" />
        </svg>
      </div>

      {/* Block 2 */}
      <div className="flex-shrink-0 w-32">
        <div className="bg-white rounded-lg border-2 border-teal-500 p-3 shadow-sm">
          <div className="text-xs text-gray-500 mb-1">Check #2</div>
          <div className="text-sm font-medium text-gray-900">Backup</div>
          <div className="mt-2 font-mono text-xs bg-gray-100 p-1 rounded truncate">
            prev: a3f2...
          </div>
        </div>
        <div className="text-center text-xs text-gray-500 mt-1">9:05 AM</div>
      </div>

      {/* Arrow */}
      <div className="flex-shrink-0 flex items-center">
        <div className="w-8 h-0.5 bg-teal-400"></div>
        <svg className="w-4 h-4 text-teal-400 -ml-1" fill="currentColor" viewBox="0 0 20 20">
          <path fillRule="evenodd" d="M10.293 3.293a1 1 0 011.414 0l6 6a1 1 0 010 1.414l-6 6a1 1 0 01-1.414-1.414L14.586 11H3a1 1 0 110-2h11.586l-4.293-4.293a1 1 0 010-1.414z" clipRule="evenodd" />
        </svg>
      </div>

      {/* Block 3 */}
      <div className="flex-shrink-0 w-32">
        <div className="bg-white rounded-lg border-2 border-teal-500 p-3 shadow-sm">
          <div className="text-xs text-gray-500 mb-1">Check #3</div>
          <div className="text-sm font-medium text-gray-900">Antivirus</div>
          <div className="mt-2 font-mono text-xs bg-gray-100 p-1 rounded truncate">
            prev: b7d4...
          </div>
        </div>
        <div className="text-center text-xs text-gray-500 mt-1">9:10 AM</div>
      </div>

      {/* More indicator */}
      <div className="flex-shrink-0 text-gray-400 text-2xl px-2">...</div>
    </div>

    <p className="text-sm text-gray-600 text-center mt-4">
      Each evidence bundle references the previous one, creating a tamper-evident chain.
      Any modification would invalidate the chain and be detectable during verification.
    </p>
  </div>
);

// Visual component for dashboard walkthrough
const DashboardWalkthrough: React.FC = () => (
  <div className="my-6 space-y-4">
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      {/* Mock header */}
      <div className="bg-gray-50 border-b border-gray-200 px-4 py-3 flex items-center gap-3">
        <div className="w-8 h-8 bg-teal-100 rounded-lg flex items-center justify-center">
          <svg className="w-4 h-4 text-teal-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
          </svg>
        </div>
        <span className="font-medium text-gray-700">Your Practice Name</span>
      </div>

      {/* KPI Cards mockup */}
      <div className="p-4">
        <div className="grid grid-cols-4 gap-3">
          <div className="bg-gray-50 rounded-lg p-3 relative">
            <div className="absolute -top-2 -right-2 w-6 h-6 bg-blue-500 text-white rounded-full flex items-center justify-center text-xs font-bold">1</div>
            <div className="text-xs text-gray-500">Compliance Score</div>
            <div className="text-lg font-bold text-green-600">92%</div>
          </div>
          <div className="bg-gray-50 rounded-lg p-3 relative">
            <div className="absolute -top-2 -right-2 w-6 h-6 bg-blue-500 text-white rounded-full flex items-center justify-center text-xs font-bold">2</div>
            <div className="text-xs text-gray-500">Controls Passed</div>
            <div className="text-lg font-bold text-gray-900">12/13</div>
          </div>
          <div className="bg-gray-50 rounded-lg p-3 relative">
            <div className="absolute -top-2 -right-2 w-6 h-6 bg-blue-500 text-white rounded-full flex items-center justify-center text-xs font-bold">3</div>
            <div className="text-xs text-gray-500">Issues</div>
            <div className="text-lg font-bold text-red-600">1</div>
          </div>
          <div className="bg-gray-50 rounded-lg p-3 relative">
            <div className="absolute -top-2 -right-2 w-6 h-6 bg-blue-500 text-white rounded-full flex items-center justify-center text-xs font-bold">4</div>
            <div className="text-xs text-gray-500">Sites</div>
            <div className="text-lg font-bold text-gray-900">1</div>
          </div>
        </div>
      </div>
    </div>

    {/* Legend */}
    <div className="grid grid-cols-2 gap-3 text-sm">
      <div className="flex items-start gap-2">
        <span className="w-5 h-5 bg-blue-500 text-white rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0">1</span>
        <span className="text-gray-600"><strong>Compliance Score</strong> - Your overall HIPAA compliance percentage</span>
      </div>
      <div className="flex items-start gap-2">
        <span className="w-5 h-5 bg-blue-500 text-white rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0">2</span>
        <span className="text-gray-600"><strong>Controls Passed</strong> - How many checks are passing vs total</span>
      </div>
      <div className="flex items-start gap-2">
        <span className="w-5 h-5 bg-blue-500 text-white rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0">3</span>
        <span className="text-gray-600"><strong>Issues</strong> - Failed checks requiring attention</span>
      </div>
      <div className="flex items-start gap-2">
        <span className="w-5 h-5 bg-blue-500 text-white rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0">4</span>
        <span className="text-gray-600"><strong>Sites</strong> - Number of locations being monitored</span>
      </div>
    </div>
  </div>
);

// Evidence download walkthrough
const EvidenceDownloadSteps: React.FC = () => (
  <div className="my-6">
    <div className="space-y-4">
      {/* Step 1 */}
      <div className="flex gap-4">
        <div className="flex-shrink-0 w-8 h-8 bg-teal-500 text-white rounded-full flex items-center justify-center font-bold">1</div>
        <div className="flex-1">
          <h5 className="font-medium text-gray-900">Navigate to Evidence Archive</h5>
          <p className="text-sm text-gray-600 mt-1">Click "Evidence Archive" from the dashboard quick links</p>
          <div className="mt-2 bg-gray-50 rounded-lg p-3 flex items-center gap-3">
            <div className="w-10 h-10 bg-teal-100 rounded-lg flex items-center justify-center">
              <svg className="w-5 h-5 text-teal-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            </div>
            <div>
              <div className="font-medium text-gray-900 text-sm">Evidence Archive</div>
              <div className="text-xs text-gray-500">View and download compliance evidence</div>
            </div>
          </div>
        </div>
      </div>

      {/* Step 2 */}
      <div className="flex gap-4">
        <div className="flex-shrink-0 w-8 h-8 bg-teal-500 text-white rounded-full flex items-center justify-center font-bold">2</div>
        <div className="flex-1">
          <h5 className="font-medium text-gray-900">Find the Evidence You Need</h5>
          <p className="text-sm text-gray-600 mt-1">Use filters to find specific check types, dates, or HIPAA controls</p>
          <div className="mt-2 bg-gray-50 rounded-lg p-3">
            <div className="flex items-center gap-2 text-sm">
              <span className="px-2 py-1 bg-white border border-gray-200 rounded text-gray-700">All Results</span>
              <span className="text-gray-400">|</span>
              <span className="text-gray-500">97,891 total bundles</span>
            </div>
          </div>
        </div>
      </div>

      {/* Step 3 */}
      <div className="flex gap-4">
        <div className="flex-shrink-0 w-8 h-8 bg-teal-500 text-white rounded-full flex items-center justify-center font-bold">3</div>
        <div className="flex-1">
          <h5 className="font-medium text-gray-900">Download or View Details</h5>
          <p className="text-sm text-gray-600 mt-1">Click the eye icon to view details, or download icon to get the bundle</p>
          <div className="mt-2 bg-gray-50 rounded-lg p-3 flex items-center justify-between">
            <div className="text-sm">
              <div className="text-gray-900">windows_backup_status</div>
              <div className="text-xs text-gray-500">164.312(b) - Audit Controls</div>
            </div>
            <div className="flex items-center gap-2">
              <span className="px-2 py-0.5 bg-green-100 text-green-700 text-xs rounded">Pass</span>
              <button className="p-1.5 hover:bg-gray-200 rounded" title="View">
                <svg className="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                </svg>
              </button>
              <button className="p-1.5 hover:bg-gray-200 rounded" title="Download">
                <svg className="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                </svg>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
);

// Auditor explanation component
const AuditorExplanation: React.FC = () => (
  <div className="my-6 bg-amber-50 border border-amber-200 rounded-xl p-6">
    <div className="flex items-start gap-4">
      <div className="flex-shrink-0 w-12 h-12 bg-amber-100 rounded-full flex items-center justify-center">
        <svg className="w-6 h-6 text-amber-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      </div>
      <div>
        <h4 className="font-semibold text-amber-900 mb-2">What to Tell Your Auditor</h4>
        <div className="space-y-3 text-sm text-amber-800">
          <p>
            <strong>About the Evidence Chain:</strong> "Our compliance evidence uses cryptographic hash chains,
            similar to blockchain technology. Each piece of evidence contains a reference to the previous one,
            making it impossible to alter historical records without detection."
          </p>
          <p>
            <strong>About Digital Signatures:</strong> "Every evidence bundle is digitally signed using Ed25519
            cryptography by the on-premises appliance at the time of collection, proving authenticity and
            preventing tampering."
          </p>
          <p>
            <strong>About Timestamps:</strong> "Timestamps are synchronized with NTP servers and cryptographically
            bound to each evidence bundle, providing verifiable proof of when each check occurred."
          </p>
        </div>
      </div>
    </div>
  </div>
);

export const ClientHelp: React.FC = () => {
  const navigate = useNavigate();
  const { user } = useClient();
  const [expandedSection, setExpandedSection] = useState<string | null>('getting-started');

  if (!user) {
    navigate('/client/login');
    return null;
  }

  const sections: HelpSection[] = [
    {
      id: 'getting-started',
      title: 'Getting Started',
      icon: '🚀',
      content: (
        <div className="space-y-4">
          <h4 className="font-medium text-gray-900">Welcome to OsirisCare</h4>
          <p className="text-gray-600">
            Your compliance portal gives you visibility into your HIPAA compliance status,
            evidence collection, and audit readiness.
          </p>
          <DashboardWalkthrough />
        </div>
      ),
    },
    {
      id: 'evidence-chain',
      title: 'Evidence Chain & Blockchain Verification',
      icon: '🔗',
      content: (
        <div className="space-y-4">
          <p className="text-gray-600">
            OsirisCare uses cryptographic hash chains (similar to blockchain) to ensure your
            compliance evidence is tamper-proof and verifiable.
          </p>
          <EvidenceChainDiagram />
          <div className="bg-teal-50 p-4 rounded-lg">
            <h5 className="font-medium text-teal-800 mb-2">Key Features</h5>
            <ul className="list-disc list-inside text-teal-700 space-y-2 text-sm">
              <li><strong>Immutable Records</strong> - Once recorded, evidence cannot be altered</li>
              <li><strong>Digital Signatures</strong> - Ed25519 cryptographic signatures prove authenticity</li>
              <li><strong>Chain Verification</strong> - Each bundle links to the previous, creating an audit trail</li>
              <li><strong>Timestamping</strong> - NTP-synchronized times prove when checks occurred</li>
            </ul>
          </div>
          <AuditorExplanation />
        </div>
      ),
    },
    {
      id: 'downloading-evidence',
      title: 'Downloading Evidence for Audits',
      icon: '📥',
      content: (
        <div className="space-y-4">
          <p className="text-gray-600">
            When preparing for a HIPAA audit, you'll need to provide evidence of your compliance controls.
            Here's how to download what you need:
          </p>
          <EvidenceDownloadSteps />
          <div className="bg-blue-50 p-4 rounded-lg">
            <h5 className="font-medium text-blue-800 mb-2">Evidence Bundle Contents</h5>
            <p className="text-blue-700 text-sm mb-3">Each downloaded bundle includes:</p>
            <ul className="grid grid-cols-2 gap-2 text-sm text-blue-700">
              <li className="flex items-center gap-2">
                <svg className="w-4 h-4 text-blue-500" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                </svg>
                Check result (Pass/Fail/Warn)
              </li>
              <li className="flex items-center gap-2">
                <svg className="w-4 h-4 text-blue-500" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                </svg>
                Timestamp (verified)
              </li>
              <li className="flex items-center gap-2">
                <svg className="w-4 h-4 text-blue-500" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                </svg>
                HIPAA control mapping
              </li>
              <li className="flex items-center gap-2">
                <svg className="w-4 h-4 text-blue-500" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                </svg>
                Digital signature
              </li>
              <li className="flex items-center gap-2">
                <svg className="w-4 h-4 text-blue-500" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                </svg>
                Chain hash (prev link)
              </li>
              <li className="flex items-center gap-2">
                <svg className="w-4 h-4 text-blue-500" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                </svg>
                Raw check data
              </li>
            </ul>
          </div>
        </div>
      ),
    },
    {
      id: 'compliance-score',
      title: 'Understanding Your Compliance Score',
      icon: '📊',
      content: (
        <div className="space-y-4">
          <p className="text-gray-600">
            Your compliance score reflects the percentage of HIPAA controls that are passing.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="bg-green-50 p-4 rounded-lg border-l-4 border-green-500">
              <div className="text-2xl font-bold text-green-600">90-100%</div>
              <div className="text-sm font-medium text-green-800">Excellent</div>
              <div className="text-xs text-green-700 mt-1">Audit ready - minimal risk</div>
            </div>
            <div className="bg-yellow-50 p-4 rounded-lg border-l-4 border-yellow-500">
              <div className="text-2xl font-bold text-yellow-600">70-89%</div>
              <div className="text-sm font-medium text-yellow-800">Good</div>
              <div className="text-xs text-yellow-700 mt-1">Minor issues to address</div>
            </div>
            <div className="bg-red-50 p-4 rounded-lg border-l-4 border-red-500">
              <div className="text-2xl font-bold text-red-600">Below 70%</div>
              <div className="text-sm font-medium text-red-800">Needs Attention</div>
              <div className="text-xs text-red-700 mt-1">Contact your IT provider</div>
            </div>
          </div>
          <p className="text-gray-600 text-sm">
            The score updates automatically as compliance checks run throughout the day.
          </p>
        </div>
      ),
    },
    {
      id: 'hipaa-controls',
      title: 'HIPAA Controls Reference',
      icon: '🏥',
      content: (
        <div className="space-y-4">
          <p className="text-gray-600">
            Each compliance check maps to specific HIPAA Security Rule requirements. Here's what they mean:
          </p>
          <div className="space-y-3">
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <div className="flex items-start gap-3">
                <div className="w-8 h-8 bg-teal-100 rounded flex items-center justify-center flex-shrink-0">
                  <span className="text-teal-600 font-bold text-xs">a</span>
                </div>
                <div>
                  <div className="font-medium text-gray-900">164.312(a)(1) - Access Control</div>
                  <div className="text-sm text-gray-600 mt-1">
                    User authentication, unique user IDs, automatic logoff policies
                  </div>
                  <div className="text-xs text-gray-500 mt-2">
                    <strong>Checks:</strong> Password policy, account lockout, session timeouts
                  </div>
                </div>
              </div>
            </div>
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <div className="flex items-start gap-3">
                <div className="w-8 h-8 bg-teal-100 rounded flex items-center justify-center flex-shrink-0">
                  <span className="text-teal-600 font-bold text-xs">b</span>
                </div>
                <div>
                  <div className="font-medium text-gray-900">164.312(b) - Audit Controls</div>
                  <div className="text-sm text-gray-600 mt-1">
                    Recording and examining system activity, backup verification
                  </div>
                  <div className="text-xs text-gray-500 mt-2">
                    <strong>Checks:</strong> Event logging, backup status, log retention
                  </div>
                </div>
              </div>
            </div>
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <div className="flex items-start gap-3">
                <div className="w-8 h-8 bg-teal-100 rounded flex items-center justify-center flex-shrink-0">
                  <span className="text-teal-600 font-bold text-xs">c</span>
                </div>
                <div>
                  <div className="font-medium text-gray-900">164.312(c)(1) - Integrity</div>
                  <div className="text-sm text-gray-600 mt-1">
                    Protecting ePHI from improper alteration or destruction
                  </div>
                  <div className="text-xs text-gray-500 mt-2">
                    <strong>Checks:</strong> System updates, configuration drift, file integrity
                  </div>
                </div>
              </div>
            </div>
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <div className="flex items-start gap-3">
                <div className="w-8 h-8 bg-teal-100 rounded flex items-center justify-center flex-shrink-0">
                  <span className="text-teal-600 font-bold text-xs">d</span>
                </div>
                <div>
                  <div className="font-medium text-gray-900">164.312(d) - Person or Entity Authentication</div>
                  <div className="text-sm text-gray-600 mt-1">
                    Verifying that persons seeking access are who they claim to be
                  </div>
                  <div className="text-xs text-gray-500 mt-2">
                    <strong>Checks:</strong> Password complexity, MFA status, user verification
                  </div>
                </div>
              </div>
            </div>
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <div className="flex items-start gap-3">
                <div className="w-8 h-8 bg-teal-100 rounded flex items-center justify-center flex-shrink-0">
                  <span className="text-teal-600 font-bold text-xs">e</span>
                </div>
                <div>
                  <div className="font-medium text-gray-900">164.312(e)(1) - Transmission Security</div>
                  <div className="text-sm text-gray-600 mt-1">
                    Protecting ePHI during electronic transmission
                  </div>
                  <div className="text-xs text-gray-500 mt-2">
                    <strong>Checks:</strong> Firewall config, encryption (BitLocker), network security
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      ),
    },
    {
      id: 'users',
      title: 'Managing Team Members',
      icon: '👥',
      content: (
        <div className="space-y-4">
          <p className="text-gray-600">
            Add team members to give them access to the compliance portal.
          </p>
          <h5 className="font-medium text-gray-900">User Roles</h5>
          <div className="space-y-2">
            <div className="flex items-start gap-3 p-3 bg-purple-50 rounded-lg">
              <span className="bg-purple-200 text-purple-800 px-2 py-1 rounded text-sm font-medium">Owner</span>
              <span className="text-gray-700 text-sm">Full access including billing, user management, and provider transfer requests</span>
            </div>
            <div className="flex items-start gap-3 p-3 bg-blue-50 rounded-lg">
              <span className="bg-blue-200 text-blue-800 px-2 py-1 rounded text-sm font-medium">Admin</span>
              <span className="text-gray-700 text-sm">Manage users, view all data, download evidence - everything except billing</span>
            </div>
            <div className="flex items-start gap-3 p-3 bg-gray-50 rounded-lg">
              <span className="bg-gray-200 text-gray-800 px-2 py-1 rounded text-sm font-medium">Viewer</span>
              <span className="text-gray-700 text-sm">View dashboard and evidence only - read-only access</span>
            </div>
          </div>
        </div>
      ),
    },
    {
      id: 'support',
      title: 'Getting Help & Support',
      icon: '💬',
      content: (
        <div className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <div className="flex items-center gap-3 mb-3">
                <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
                  <svg className="w-5 h-5 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
                  </svg>
                </div>
                <h5 className="font-medium text-gray-900">Technical Issues</h5>
              </div>
              <p className="text-gray-600 text-sm">
                For compliance failures, workstation issues, or technical problems, contact your IT provider (MSP) first.
              </p>
            </div>
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <div className="flex items-center gap-3 mb-3">
                <div className="w-10 h-10 bg-teal-100 rounded-lg flex items-center justify-center">
                  <svg className="w-5 h-5 text-teal-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                  </svg>
                </div>
                <h5 className="font-medium text-gray-900">Portal Support</h5>
              </div>
              <p className="text-gray-600 text-sm">
                For portal questions or account help:<br />
                <a href="mailto:support@osiriscare.net" className="text-teal-600 hover:underline">support@osiriscare.net</a>
              </p>
            </div>
          </div>
        </div>
      ),
    },
  ];

  const toggleSection = (id: string) => {
    setExpandedSection(expandedSection === id ? null : id);
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-800 via-slate-700 to-teal-900">
      {/* Header */}
      <header className="bg-white/10 backdrop-blur-sm border-b border-white/10">
        <div className="max-w-4xl mx-auto px-6 py-4 flex items-center justify-between">
          <button
            onClick={() => navigate('/client/dashboard')}
            className="flex items-center gap-2 text-white/80 hover:text-white transition-colors"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            Back to Dashboard
          </button>
          <h1 className="text-xl font-semibold text-white">Help & Documentation</h1>
          <div className="w-24" />
        </div>
      </header>

      {/* Content */}
      <main className="max-w-4xl mx-auto px-6 py-8">
        {/* Intro */}
        <div className="bg-white rounded-xl shadow-lg p-6 mb-6">
          <div className="flex items-center gap-4 mb-4">
            <div className="w-12 h-12 rounded-full bg-teal-100 flex items-center justify-center">
              <svg className="w-6 h-6 text-teal-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <div>
              <h2 className="text-xl font-semibold text-gray-900">How can we help?</h2>
              <p className="text-gray-600">Find answers to common questions about your compliance portal</p>
            </div>
          </div>
        </div>

        {/* FAQ Sections */}
        <div className="space-y-3">
          {sections.map((section) => (
            <div key={section.id} className="bg-white rounded-xl shadow-lg overflow-hidden">
              <button
                onClick={() => toggleSection(section.id)}
                className="w-full px-6 py-4 flex items-center justify-between hover:bg-gray-50 transition-colors"
              >
                <div className="flex items-center gap-3">
                  <span className="text-2xl">{section.icon}</span>
                  <span className="font-medium text-gray-900">{section.title}</span>
                </div>
                <svg
                  className={`w-5 h-5 text-gray-400 transition-transform ${
                    expandedSection === section.id ? 'rotate-180' : ''
                  }`}
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </button>
              {expandedSection === section.id && (
                <div className="px-6 pb-6 pt-2 border-t border-gray-100">
                  {section.content}
                </div>
              )}
            </div>
          ))}
        </div>

        {/* Footer */}
        <div className="mt-8 text-center text-white/60 text-sm">
          <p>Powered by OsirisCare HIPAA Compliance Platform</p>
        </div>
      </main>
    </div>
  );
};
