/**
 * Plain-language glossary for HIPAA drift / incident types.
 *
 * Round-table asked: "the end customer sees 'SCREEN_LOCK' and has no
 * idea what that means". This module maps the ~30 most common
 * incident_type and check_type tokens we ship to short plain-English
 * explanations suitable for practice managers.
 *
 * Use `explain(incidentType)` to get `{ title, why_it_matters }` or
 * null if we don't have a record for it.
 *
 * Keep entries short — never more than two sentences. Never use
 * absolute safety language ("ensures", "prevents", "guarantees").
 */

export interface Explanation {
  title: string;
  why_it_matters: string;
}

const ENTRIES: Record<string, Explanation> = {
  // Authentication / access
  SCREEN_LOCK: {
    title: 'Screen lock policy',
    why_it_matters: 'A workstation was left unlocked. HIPAA asks that screens auto-lock after inactivity so someone walking by cannot see patient info.',
  },
  PASSWORD_POLICY: {
    title: 'Password strength policy',
    why_it_matters: 'A user or system account had a password that did not meet the minimum length/complexity rule your policy specifies.',
  },
  MFA_MISSING: {
    title: 'Multi-factor authentication missing',
    why_it_matters: 'An account that should require a second factor (code, app, key) is letting in on password alone.',
  },
  ACCOUNT_LOCKOUT: {
    title: 'Account lockout threshold',
    why_it_matters: 'After a number of failed logins, the account should lock. This setting controls how brute-force-friendly the machine is.',
  },
  // Patching
  PATCH_MISSING: {
    title: 'Security update not installed',
    why_it_matters: 'A published security patch has not been applied. Unpatched systems are the #1 way attackers get in.',
  },
  CRITICAL_PATCH: {
    title: 'Critical OS update',
    why_it_matters: 'A high-severity operating-system patch is available. This is the strongest signal on a device for update-now priority.',
  },
  // Backup
  BACKUP_MISSING: {
    title: 'Backup not completed',
    why_it_matters: 'Expected backup did not run or was not verified. If data is lost, recovery may not be possible without this.',
  },
  BACKUP_STALE: {
    title: 'Backup too old',
    why_it_matters: 'The most recent backup is older than your policy allows. Any data created since is at risk.',
  },
  // Malware / EDR
  AV_MISSING: {
    title: 'Anti-virus not running',
    why_it_matters: 'The endpoint protection service is stopped or missing. Malicious code has fewer things blocking it.',
  },
  EDR_MISSING: {
    title: 'Endpoint detection (EDR) inactive',
    why_it_matters: 'The EDR agent that monitors for threats is not running on this machine.',
  },
  DEFENDER_DISABLED: {
    title: 'Windows Defender disabled',
    why_it_matters: 'The built-in Windows virus protection is off. Often this is done intentionally (another product is active) but worth confirming.',
  },
  RANSOMWARE_INDICATOR: {
    title: 'Possible ransomware activity',
    why_it_matters: 'Behavior pattern associated with ransomware was observed. This is investigated immediately by an IT technician.',
  },
  // Audit / logging
  AUDIT_DISABLED: {
    title: 'Audit logging disabled',
    why_it_matters: 'Security logs that prove who did what are not being recorded. HIPAA §164.312(b) requires audit controls.',
  },
  AUDIT_LOG_CLEARED: {
    title: 'Audit log was cleared',
    why_it_matters: 'An event 1102 was detected — the Security event log was wiped. This is a significant signal.',
  },
  // Network / firewall
  FIREWALL_DISABLED: {
    title: 'Firewall turned off',
    why_it_matters: 'The Windows/Linux firewall is not blocking unexpected inbound connections.',
  },
  SMB_SIGNING: {
    title: 'File-sharing signing disabled',
    why_it_matters: 'Unsigned SMB traffic can be tampered with in transit. This setting enforces signing.',
  },
  // Encryption
  BITLOCKER_MISSING: {
    title: 'Disk encryption off',
    why_it_matters: 'If the laptop is stolen, patient data could be read off the drive without the login password.',
  },
  TLS_OUTDATED: {
    title: 'Outdated TLS in use',
    why_it_matters: 'A service is negotiating TLS 1.0/1.1 which is deprecated. Modern TLS 1.2+ is expected.',
  },
  // DNS / services
  DNS_CONFIG: {
    title: 'DNS configuration drift',
    why_it_matters: 'DNS settings have deviated from what your network plan expects. This can make some services unreachable.',
  },
  SERVICE_DOWN: {
    title: 'Service stopped',
    why_it_matters: 'A system service that should always be running has stopped.',
  },
  // Connectivity
  APPLIANCE_OFFLINE: {
    title: 'Appliance offline',
    why_it_matters: 'Your OsirisCare appliance hasn\'t checked in. Until it recovers, new configuration drift is NOT being detected.',
  },
};

// Helper — tolerant of case + minor formatting differences.
export function explain(incidentType: string | null | undefined): Explanation | null {
  if (!incidentType) return null;
  const norm = incidentType.toUpperCase().replace(/[^A-Z0-9_]/g, '_');
  return ENTRIES[norm] || null;
}

export const GLOSSARY = ENTRIES;
