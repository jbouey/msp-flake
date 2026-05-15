import React from 'react';
import { Link, useParams } from 'react-router-dom';
import { OsirisCareLeaf } from '../components/shared';
import { BRANDING } from '../constants';

const LEGAL_CONTENT: Record<string, { title: string; content: string[] }> = {
  privacy: {
    title: 'Privacy Policy',
    content: [
      'Effective Date: April 1, 2026',
      'OsirisCare ("we", "us", "our") operates the OsirisCare compliance monitoring platform. This Privacy Policy describes how we collect, use, and protect information when you use our services.',
      'Information We Collect: We collect infrastructure telemetry data (system configuration state, compliance scan results, remediation activity logs) from appliances deployed at your facilities. This data is used to provide compliance monitoring and issue detection services. All data transmitted from appliances is scrubbed of Protected Health Information (PHI) at the appliance level before reaching our servers. Our central infrastructure is designed to operate under a PHI-scrubbed architecture.',
      'How We Use Information: Infrastructure telemetry is used solely to provide compliance monitoring, generate evidence bundles, produce compliance reports, and operate the healing pipeline. We do not sell, rent, or share your data with third parties except as required to provide the service or as required by law.',
      'Data Security: All evidence bundles are cryptographically signed (Ed25519), hash-chained (SHA-256), and blockchain-timestamped (OpenTimestamps). Data in transit is encrypted via TLS. Data at rest is encrypted. Multi-tenant isolation is enforced via row-level security across all database tables.',
      'Data Retention: Compliance evidence is retained for the duration of your subscription plus 7 years (matching HIPAA record retention requirements). You may request data export or deletion at any time.',
      'Your Rights: You may access, correct, or delete your data by contacting us. You may request a full data export at any time. There is no lock-in period.',
      'Contact: legal@osiriscare.net',
    ],
  },
  terms: {
    title: 'Terms of Service',
    content: [
      'Effective Date: April 1, 2026',
      'These Terms of Service govern your use of the OsirisCare compliance monitoring platform.',
      'Service Description: OsirisCare provides automated compliance monitoring tools including continuous compliance scanning, evidence capture, auto-healing, and compliance reporting for healthcare organizations. The service operates via on-premise appliances that monitor your infrastructure and report to our central management platform.',
      'Your Responsibilities: You are solely responsible for your organization\'s compliance program, policies, regulatory obligations, and remediation decisions. OsirisCare is a monitoring and automation tool that supports your compliance efforts \u2014 it does not replace your compliance program, provide legal advice, or guarantee regulatory compliance.',
      'Operator Authorization: All remediation actions performed by the platform are governed by rules that your designated operators configure and approve. L1 deterministic healing operates according to pre-approved runbooks. L2 intelligent healing operates within parameters you set. L3 escalations always require human authorization.',
      'Availability: We target 99.9% uptime for the central management platform. On-premise appliances operate independently and continue scanning during connectivity interruptions. Evidence is cached locally and synchronized when connectivity is restored.',
      'Limitation of Liability: OsirisCare\'s total liability is limited to the fees paid in the 12 months preceding the claim. We are not liable for regulatory penalties, audit findings, or compliance failures, as compliance responsibility rests with your organization.',
      'Termination: Either party may terminate with 30 days written notice. Upon termination, you may request a full data export. On-premise appliances are returned or decommissioned.',
      'Contact: legal@osiriscare.net',
    ],
  },
  baa: {
    title: 'Business Associate Agreement',
    content: [
      'Version: v1.0-INTERIM. Effective Date: May 13, 2026. The current master Business Associate Agreement is a HIPAA-core compliance instrument derived from the U.S. Department of Health & Human Services sample BAA provisions (45 CFR \u00a7164.504(e)(1)). It is binding upon Covered Entity e-signature in the OsirisCare signup flow.',
      'Counsel hardening in progress. Outside HIPAA counsel is hardening the commercial and legal terms (term, termination, indemnity scope, audit rights, governing law, dispute resolution) within 14\u201321 days of the v1.0-INTERIM effective date. v2.0 will supersede v1.0-INTERIM once counsel-hardening lands. Customers who sign v1.0-INTERIM will be offered the v2.0 amendment when published.',
      'Substrate posture. OsirisCare\'s platform is architected such that Protected Health Information is scrubbed at the Appliance edge by design before any data egresses to OsirisCare Central Command. Under normal operation, Central Command does not receive, maintain, or transmit PHI. This is an architectural commitment and not an absence-proof. The technical implementation is documented in Exhibit B (Data Flow Disclosure) of the master BAA.',
      'Coverage. The agreement covers OsirisCare\'s services as a Business Associate to the Covered Entity, including operation of on-premise monitoring appliances, OsirisCare Central Command management infrastructure, evidence storage, compliance reporting, and audit-supportive technical evidence generation. Standard HIPAA provisions for breach notification, safeguards, permitted uses and disclosures, accounting of disclosures, subcontractor flow-down, and termination are included.',
      'How to view or sign. The full master BAA is presented in the OsirisCare signup flow for e-signature before any BAA-gated workflow (ingest, evidence export, owner transfer, cross-org site relocate) becomes available. Existing customers and prospective customers may also request the master BAA text by emailing administrator@osiriscare.net.',
      'Responsibility structure. The BAA does not alter the fundamental HIPAA responsibility structure \u2014 your organization remains the Covered Entity responsible for its HIPAA compliance program. OsirisCare operates as the Business Associate providing compliance monitoring substrate, evidence capture, and operator-authorized remediation workflows.',
    ],
  },
};

export const Legal: React.FC = () => {
  const { page } = useParams<{ page: string }>();
  const content = LEGAL_CONTENT[page || ''];

  if (!content) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center">
        <div className="text-center">
          <p className="text-slate-500">Page not found</p>
          <Link to="/" className="text-teal-600 hover:underline mt-2 inline-block">Back to home</Link>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-white" style={{ fontFamily: "'DM Sans', 'Helvetica Neue', system-ui, sans-serif" }}>
      <style>{`@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600&display=swap');`}</style>

      <nav className="border-b border-slate-100 bg-white">
        <div className="max-w-3xl mx-auto px-6 h-16 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-3">
            <div
              className="w-8 h-8 rounded-lg flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
            >
              <OsirisCareLeaf className="w-4 h-4" color="white" />
            </div>
            <span className="text-base font-semibold text-slate-900">{BRANDING.name}</span>
          </Link>
          <Link to="/" className="text-sm text-slate-500 hover:text-slate-900">Back to home</Link>
        </div>
      </nav>

      <main className="max-w-3xl mx-auto px-6 py-16">
        <h1 className="text-3xl font-bold text-slate-900 mb-8">{content.title}</h1>
        {content.content.map((para, i) => (
          <p key={i} className="text-sm text-slate-600 leading-relaxed mb-6">{para}</p>
        ))}
        <div className="border-t border-slate-100 pt-8 mt-12">
          <p className="text-xs text-slate-400">
            &copy; {new Date().getFullYear()} {BRANDING.name}. All rights reserved.
          </p>
        </div>
      </main>
    </div>
  );
};

export default Legal;
