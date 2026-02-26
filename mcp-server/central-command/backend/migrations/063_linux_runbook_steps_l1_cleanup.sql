-- Migration 063: Populate Linux runbook steps + consolidate L1 rules
-- Production-ready: runbooks have meaningful steps, L1 rules deduplicated

BEGIN;

-- ============================================================
-- PART 1: Populate runbook steps for core Linux incident types
-- These match what the Go daemon actually executes locally
-- ============================================================

-- linux_firewall → L1-LIN-FW-001
UPDATE runbooks SET steps = '[
  {"name": "detect_distro", "command": "cat /etc/os-release | grep ^ID=", "description": "Detect Linux distribution"},
  {"name": "install_ufw", "command": "apt-get install -y ufw || yum install -y firewalld", "description": "Install firewall package"},
  {"name": "enable_firewall", "command": "ufw --force enable || systemctl enable --now firewalld", "description": "Enable and start firewall"},
  {"name": "default_deny", "command": "ufw default deny incoming && ufw default allow outgoing || firewall-cmd --set-default-zone=drop", "description": "Set default deny incoming"},
  {"name": "allow_ssh", "command": "ufw allow ssh || firewall-cmd --permanent --add-service=ssh && firewall-cmd --reload", "description": "Whitelist SSH access"},
  {"name": "verify", "command": "ufw status || firewall-cmd --state", "description": "Verify firewall is active"}
]'::jsonb
WHERE runbook_id = 'L1-LIN-FW-001';

-- linux_ssh_config → L1-LIN-SSH-001
UPDATE runbooks SET steps = '[
  {"name": "backup_config", "command": "cp /etc/ssh/sshd_config /etc/ssh/sshd_config.bak.$(date +%s)", "description": "Backup SSH config"},
  {"name": "disable_root_login", "command": "sed -i ''s/^#*PermitRootLogin.*/PermitRootLogin no/'' /etc/ssh/sshd_config", "description": "Disable root SSH login"},
  {"name": "disable_password_auth", "command": "sed -i ''s/^#*PasswordAuthentication.*/PasswordAuthentication no/'' /etc/ssh/sshd_config", "description": "Disable password authentication"},
  {"name": "set_max_auth", "command": "sed -i ''s/^#*MaxAuthTries.*/MaxAuthTries 3/'' /etc/ssh/sshd_config", "description": "Limit auth attempts to 3"},
  {"name": "set_idle_timeout", "command": "grep -q ClientAliveInterval /etc/ssh/sshd_config && sed -i ''s/^#*ClientAliveInterval.*/ClientAliveInterval 300/'' /etc/ssh/sshd_config || echo ''ClientAliveInterval 300'' >> /etc/ssh/sshd_config", "description": "Set 5-minute idle timeout"},
  {"name": "reload_sshd", "command": "systemctl reload sshd", "description": "Reload SSH daemon"},
  {"name": "verify", "command": "sshd -T | grep -E ''permitrootlogin|passwordauthentication|maxauthtries''", "description": "Verify SSH hardening"}
]'::jsonb
WHERE runbook_id = 'L1-LIN-SSH-001';

-- linux_kernel_params → L1-LIN-KERN-001
UPDATE runbooks SET steps = '[
  {"name": "apply_sysctl", "command": "sysctl -w net.ipv4.ip_forward=0 net.ipv4.conf.all.send_redirects=0 net.ipv4.conf.all.accept_redirects=0 kernel.randomize_va_space=2 fs.suid_dumpable=0", "description": "Apply kernel hardening params"},
  {"name": "apply_network", "command": "sysctl -w net.ipv4.tcp_syncookies=1 net.ipv4.icmp_echo_ignore_broadcasts=1 net.ipv4.conf.all.rp_filter=1 net.ipv4.conf.all.log_martians=1", "description": "Apply network hardening params"},
  {"name": "persist_params", "command": "cat > /etc/sysctl.d/99-hipaa-hardening.conf << ''SYSCTL''\nnet.ipv4.ip_forward=0\nnet.ipv4.conf.all.send_redirects=0\nnet.ipv4.conf.all.accept_redirects=0\nkernel.randomize_va_space=2\nfs.suid_dumpable=0\nnet.ipv4.tcp_syncookies=1\nnet.ipv4.icmp_echo_ignore_broadcasts=1\nnet.ipv4.conf.all.rp_filter=1\nnet.ipv4.conf.all.log_martians=1\nSYSCTL", "description": "Persist to sysctl.d config"},
  {"name": "blacklist_modules", "command": "printf ''install usb-storage /bin/true\\ninstall firewire-core /bin/true\\ninstall bluetooth /bin/true\\n'' > /etc/modprobe.d/hipaa-blacklist.conf", "description": "Blacklist unnecessary kernel modules"},
  {"name": "reload", "command": "sysctl --system", "description": "Reload all sysctl configs"},
  {"name": "verify", "command": "sysctl net.ipv4.ip_forward kernel.randomize_va_space fs.suid_dumpable", "description": "Verify kernel params applied"}
]'::jsonb
WHERE runbook_id = 'L1-LIN-KERN-001';

-- linux_failed_services → L1-LIN-SVC-001
UPDATE runbooks SET steps = '[
  {"name": "identify_failed", "command": "systemctl --failed --no-pager", "description": "List failed services"},
  {"name": "restart_sshd", "command": "systemctl enable sshd && systemctl start sshd", "description": "Ensure SSH daemon running"},
  {"name": "restart_auditd", "command": "systemctl enable auditd && systemctl start auditd", "description": "Ensure audit daemon running"},
  {"name": "restart_rsyslog", "command": "systemctl enable rsyslog && systemctl start rsyslog", "description": "Ensure syslog running"},
  {"name": "disable_telnet", "command": "systemctl stop telnet.socket 2>/dev/null; systemctl disable telnet.socket 2>/dev/null; true", "description": "Disable telnet if present"},
  {"name": "verify", "command": "systemctl --failed --no-pager | grep -c failed || echo 0", "description": "Verify no failed services"}
]'::jsonb
WHERE runbook_id = 'L1-LIN-SVC-001';

-- linux_suid_binaries → L1-SUID-001
UPDATE runbooks SET steps = '[
  {"name": "find_unauthorized", "command": "find /tmp /var/tmp /dev/shm -perm -4000 -type f 2>/dev/null", "description": "Find SUID binaries in temp directories"},
  {"name": "remove_suid", "command": "find /tmp /var/tmp /dev/shm -perm -4000 -type f -exec rm -f {} \\;", "description": "Remove unauthorized SUID binaries"},
  {"name": "verify", "command": "find /tmp /var/tmp /dev/shm -perm -4000 -type f 2>/dev/null | wc -l", "description": "Verify no SUID files remain"}
]'::jsonb
WHERE runbook_id = 'L1-SUID-001';

-- linux_log_forwarding → L1-LIN-LOG-001
UPDATE runbooks SET steps = '[
  {"name": "configure_journald", "command": "mkdir -p /etc/systemd/journald.conf.d && printf ''[Journal]\\nStorage=persistent\\nMaxRetentionSec=7776000\\n'' > /etc/systemd/journald.conf.d/hipaa-retention.conf", "description": "Set journald to 90-day persistent retention"},
  {"name": "configure_logrotate", "command": "sed -i ''s/^rotate .*/rotate 90/'' /etc/logrotate.conf", "description": "Set logrotate to 90-day retention"},
  {"name": "restart_journald", "command": "systemctl restart systemd-journald", "description": "Apply journald config"},
  {"name": "verify", "command": "journalctl --disk-usage && cat /etc/systemd/journald.conf.d/hipaa-retention.conf", "description": "Verify log retention config"}
]'::jsonb
WHERE runbook_id = 'L1-LIN-LOG-001';

-- linux_audit_logging → L1-LIN-AUDIT-001
UPDATE runbooks SET steps = '[
  {"name": "install_auditd", "command": "apt-get install -y auditd audispd-plugins 2>/dev/null || yum install -y audit 2>/dev/null || true", "description": "Install auditd if missing"},
  {"name": "enable_auditd", "command": "systemctl enable auditd && systemctl start auditd", "description": "Enable and start audit daemon"},
  {"name": "add_identity_rules", "command": "printf ''-w /etc/passwd -p wa -k identity\\n-w /etc/shadow -p wa -k identity\\n-w /etc/group -p wa -k identity\\n-w /etc/gshadow -p wa -k identity\\n'' > /etc/audit/rules.d/identity.rules", "description": "Add identity file audit rules"},
  {"name": "add_auth_rules", "command": "printf ''-w /var/log/auth.log -p wa -k auth\\n-w /var/log/secure -p wa -k auth\\n-w /etc/pam.d/ -p wa -k pam\\n'' > /etc/audit/rules.d/auth.rules", "description": "Add authentication audit rules"},
  {"name": "reload_rules", "command": "augenrules --load", "description": "Load audit rules"},
  {"name": "verify", "command": "auditctl -l | wc -l", "description": "Verify audit rules loaded"}
]'::jsonb
WHERE runbook_id = 'L1-LIN-AUDIT-001';

-- linux_ntp_sync → L1-LIN-NTP-001
UPDATE runbooks SET steps = '[
  {"name": "install_chrony", "command": "apt-get install -y chrony 2>/dev/null || yum install -y chrony 2>/dev/null || true", "description": "Install chrony NTP client"},
  {"name": "configure_ntp", "command": "printf ''server 0.pool.ntp.org iburst\\nserver 1.pool.ntp.org iburst\\nserver 2.pool.ntp.org iburst\\nserver 3.pool.ntp.org iburst\\ndriftfile /var/lib/chrony/drift\\nmakestep 1.0 3\\nrtcsync\\nlogdir /var/log/chrony\\n'' > /etc/chrony.conf", "description": "Configure NTP servers"},
  {"name": "enable_chrony", "command": "systemctl enable chronyd && systemctl restart chronyd", "description": "Enable and restart chrony"},
  {"name": "force_sync", "command": "chronyc makestep", "description": "Force immediate time sync"},
  {"name": "verify", "command": "chronyc tracking | grep -E ''Leap status|System time''", "description": "Verify NTP synchronization"}
]'::jsonb
WHERE runbook_id = 'L1-LIN-NTP-001';

-- linux_file_permissions → LIN-PERM-001
UPDATE runbooks SET steps = '[
  {"name": "fix_shadow", "command": "chmod 640 /etc/shadow && chown root:shadow /etc/shadow 2>/dev/null || chown root:root /etc/shadow", "description": "Secure /etc/shadow permissions"},
  {"name": "fix_passwd", "command": "chmod 644 /etc/passwd && chown root:root /etc/passwd", "description": "Secure /etc/passwd permissions"},
  {"name": "fix_sshd_config", "command": "chmod 600 /etc/ssh/sshd_config && chown root:root /etc/ssh/sshd_config", "description": "Secure sshd_config permissions"},
  {"name": "fix_sudoers", "command": "chmod 440 /etc/sudoers && chown root:root /etc/sudoers", "description": "Secure sudoers permissions"},
  {"name": "fix_cron", "command": "chmod 600 /etc/crontab && chmod 700 /etc/cron.d /etc/cron.daily /etc/cron.hourly /etc/cron.weekly /etc/cron.monthly 2>/dev/null || true", "description": "Secure cron file permissions"},
  {"name": "remove_world_writable", "command": "find /etc -perm -0002 -type f -exec chmod o-w {} \\; 2>/dev/null || true", "description": "Remove world-writable bits from /etc"},
  {"name": "verify", "command": "stat -c ''%a %U:%G %n'' /etc/shadow /etc/passwd /etc/ssh/sshd_config /etc/sudoers", "description": "Verify file permissions"}
]'::jsonb
WHERE runbook_id = 'LIN-PERM-001';

-- linux_unattended_upgrades → LIN-UPGRADES-001
UPDATE runbooks SET steps = '[
  {"name": "update_cache", "command": "apt-get update 2>/dev/null || yum makecache 2>/dev/null || true", "description": "Update package cache"},
  {"name": "apply_security_updates", "command": "DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold 2>/dev/null || yum update -y --security 2>/dev/null || true", "description": "Apply security updates"},
  {"name": "verify", "command": "apt-get -s upgrade 2>/dev/null | grep -c ^Inst || yum check-update --security 2>/dev/null | tail -1", "description": "Verify no pending security updates"}
]'::jsonb
WHERE runbook_id = 'LIN-UPGRADES-001';

-- linux_cron_review → L1-LIN-CRON-001
UPDATE runbooks SET steps = '[
  {"name": "secure_cron_dirs", "command": "chmod 700 /etc/cron.d /etc/cron.daily /etc/cron.hourly /etc/cron.weekly /etc/cron.monthly 2>/dev/null || true", "description": "Restrict cron directory access"},
  {"name": "secure_crontab", "command": "chmod 600 /etc/crontab", "description": "Restrict crontab file"},
  {"name": "create_cron_allow", "command": "echo root > /etc/cron.allow && chmod 600 /etc/cron.allow", "description": "Restrict cron to root only"},
  {"name": "verify", "command": "stat -c ''%a'' /etc/crontab /etc/cron.d && cat /etc/cron.allow", "description": "Verify cron permissions"}
]'::jsonb
WHERE runbook_id = 'L1-LIN-CRON-001';

-- linux_disk_space → LIN-DISK-001
UPDATE runbooks SET steps = '[
  {"name": "check_usage", "command": "df -h | awk ''$5+0 > 90 {print $0}''", "description": "Identify partitions over 90% full"},
  {"name": "clean_logs", "command": "journalctl --vacuum-size=500M && find /var/log -name ''*.gz'' -mtime +30 -delete", "description": "Clean old compressed logs"},
  {"name": "clean_tmp", "command": "find /tmp -type f -atime +7 -delete 2>/dev/null; find /var/tmp -type f -atime +7 -delete 2>/dev/null || true", "description": "Clean temp files older than 7 days"},
  {"name": "clean_apt", "command": "apt-get clean 2>/dev/null || yum clean all 2>/dev/null || true", "description": "Clean package cache"},
  {"name": "verify", "command": "df -h /", "description": "Verify disk usage improved"}
]'::jsonb
WHERE runbook_id = 'LIN-DISK-001';

-- linux_cert_expiry → LIN-CERT-001
UPDATE runbooks SET steps = '[
  {"name": "check_expiry", "command": "find /etc/ssl/certs /var/lib/msp/ca -name ''*.pem'' -o -name ''*.crt'' | xargs -I{} openssl x509 -in {} -noout -enddate -subject 2>/dev/null", "description": "Check certificate expiry dates"},
  {"name": "alert", "command": "echo ''Certificate renewal requires human review — escalating''", "description": "Certificate renewal requires CA interaction"}
]'::jsonb
WHERE runbook_id = 'LIN-CERT-001';


-- ============================================================
-- PART 2: Also populate the named runbooks (LIN-* series)
-- that are referenced by the migration 050 builtin rules
-- ============================================================

UPDATE runbooks SET steps = '[
  {"name": "detect_distro", "command": "cat /etc/os-release | grep ^ID=", "description": "Detect Linux distribution"},
  {"name": "install_ufw", "command": "apt-get install -y ufw || yum install -y firewalld", "description": "Install firewall package"},
  {"name": "enable_firewall", "command": "ufw --force enable || systemctl enable --now firewalld", "description": "Enable and start firewall"},
  {"name": "allow_ssh", "command": "ufw allow ssh || firewall-cmd --permanent --add-service=ssh && firewall-cmd --reload", "description": "Whitelist SSH"},
  {"name": "verify", "command": "ufw status || firewall-cmd --state", "description": "Verify firewall active"}
]'::jsonb
WHERE runbook_id = 'LIN-FW-001' AND jsonb_array_length(steps) = 0;

UPDATE runbooks SET steps = '[
  {"name": "check_services", "command": "systemctl --failed --no-pager", "description": "List failed services"},
  {"name": "restart_critical", "command": "for svc in sshd auditd rsyslog; do systemctl enable $svc && systemctl start $svc; done 2>/dev/null || true", "description": "Restart critical services"},
  {"name": "verify", "command": "systemctl --failed --no-pager | grep -c failed || echo 0", "description": "Verify services running"}
]'::jsonb
WHERE runbook_id IN ('LIN-SVC-001', 'LIN-SVC-002', 'LIN-SVC-003') AND jsonb_array_length(steps) = 0;

UPDATE runbooks SET steps = '[
  {"name": "find_suid", "command": "find /tmp /var/tmp /dev/shm -perm -4000 -type f 2>/dev/null", "description": "Find unauthorized SUID binaries"},
  {"name": "remove", "command": "find /tmp /var/tmp /dev/shm -perm -4000 -type f -exec rm -f {} \\;", "description": "Remove unauthorized SUID"},
  {"name": "verify", "command": "find /tmp /var/tmp /dev/shm -perm -4000 -type f 2>/dev/null | wc -l", "description": "Verify clean"}
]'::jsonb
WHERE runbook_id IN ('LIN-SUID-001', 'L1-LIN-SUID-001') AND jsonb_array_length(steps) = 0;

UPDATE runbooks SET steps = '[
  {"name": "harden_ssh", "command": "sed -i ''s/^#*PermitRootLogin.*/PermitRootLogin no/;s/^#*PasswordAuthentication.*/PasswordAuthentication no/;s/^#*MaxAuthTries.*/MaxAuthTries 3/'' /etc/ssh/sshd_config", "description": "Apply SSH hardening"},
  {"name": "reload", "command": "systemctl reload sshd", "description": "Reload SSH daemon"},
  {"name": "verify", "command": "sshd -T | grep -E ''permitrootlogin|passwordauthentication''", "description": "Verify SSH config"}
]'::jsonb
WHERE runbook_id IN ('LIN-SSH-001', 'LIN-SSH-002', 'LIN-SSH-003') AND jsonb_array_length(steps) = 0;

UPDATE runbooks SET steps = '[
  {"name": "apply_hardening", "command": "sysctl -w net.ipv4.ip_forward=0 kernel.randomize_va_space=2 fs.suid_dumpable=0 net.ipv4.tcp_syncookies=1", "description": "Apply kernel hardening"},
  {"name": "persist", "command": "sysctl --system", "description": "Persist kernel params"},
  {"name": "verify", "command": "sysctl net.ipv4.ip_forward kernel.randomize_va_space", "description": "Verify params"}
]'::jsonb
WHERE runbook_id = 'LIN-KERN-001' AND jsonb_array_length(steps) = 0;

UPDATE runbooks SET steps = '[
  {"name": "configure_retention", "command": "mkdir -p /etc/systemd/journald.conf.d && printf ''[Journal]\\nStorage=persistent\\nMaxRetentionSec=7776000\\n'' > /etc/systemd/journald.conf.d/hipaa-retention.conf", "description": "Set 90-day log retention"},
  {"name": "restart", "command": "systemctl restart systemd-journald", "description": "Apply retention config"},
  {"name": "verify", "command": "journalctl --disk-usage", "description": "Verify log storage"}
]'::jsonb
WHERE runbook_id = 'LIN-LOG-001' AND jsonb_array_length(steps) = 0;

UPDATE runbooks SET steps = '[
  {"name": "install_auditd", "command": "apt-get install -y auditd 2>/dev/null || yum install -y audit 2>/dev/null || true", "description": "Install auditd"},
  {"name": "enable_start", "command": "systemctl enable auditd && systemctl start auditd", "description": "Enable audit daemon"},
  {"name": "add_rules", "command": "printf ''-w /etc/passwd -p wa -k identity\\n-w /etc/shadow -p wa -k identity\\n'' > /etc/audit/rules.d/identity.rules && augenrules --load", "description": "Add identity audit rules"},
  {"name": "verify", "command": "auditctl -l | wc -l", "description": "Verify audit rules"}
]'::jsonb
WHERE runbook_id IN ('LIN-AUDIT-001', 'LIN-AUDIT-002') AND jsonb_array_length(steps) = 0;

UPDATE runbooks SET steps = '[
  {"name": "install_chrony", "command": "apt-get install -y chrony 2>/dev/null || yum install -y chrony 2>/dev/null || true", "description": "Install chrony"},
  {"name": "configure", "command": "printf ''server 0.pool.ntp.org iburst\\nserver 1.pool.ntp.org iburst\\ndriftfile /var/lib/chrony/drift\\nmakestep 1.0 3\\nrtcsync\\n'' > /etc/chrony.conf", "description": "Configure NTP"},
  {"name": "restart", "command": "systemctl enable chronyd && systemctl restart chronyd && chronyc makestep", "description": "Start and sync"},
  {"name": "verify", "command": "chronyc tracking | grep ''Leap status''", "description": "Verify NTP sync"}
]'::jsonb
WHERE runbook_id = 'LIN-NTP-001' AND jsonb_array_length(steps) = 0;


-- ============================================================
-- PART 3: Consolidate duplicate L1 rules
-- Keep best performer per incident_type, disable duplicates
-- ============================================================

-- linux_ssh_config: Keep L1-SSH-001 (447 matches, 119 success) as primary
-- Disable the duplicates
UPDATE l1_rules SET enabled = false
WHERE incident_pattern->>'incident_type' = 'linux_ssh_config'
AND rule_id NOT IN ('L1-SSH-001')
AND rule_id NOT LIKE 'RB-%';

-- linux_suid_binaries: Keep L1-SUID-001 (381 matches, 375 success = 98%)
UPDATE l1_rules SET enabled = false
WHERE incident_pattern->>'incident_type' = 'linux_suid_binaries'
AND rule_id NOT IN ('L1-SUID-001')
AND rule_id NOT LIKE 'RB-%';

-- linux_audit_logging: Keep L1-LIN-AUDIT-001 (23 matches, 11 success)
UPDATE l1_rules SET enabled = false
WHERE incident_pattern->>'incident_type' = 'linux_audit_logging'
AND rule_id NOT IN ('L1-LIN-AUDIT-001')
AND rule_id NOT LIKE 'RB-%';

-- linux_failed_services: Keep L1-LIN-SVC-001 (199 matches, 129 success = 65%)
UPDATE l1_rules SET enabled = false
WHERE incident_pattern->>'incident_type' = 'linux_failed_services'
AND rule_id NOT IN ('L1-LIN-SVC-001')
AND rule_id NOT LIKE 'RB-%';

-- Disable all RB-AUTO-* rules that reference MISSING runbooks
UPDATE l1_rules SET enabled = false
WHERE rule_id LIKE 'RB-AUTO-%'
AND runbook_id IN ('AUTO-CRON', 'AUTO-SERVICES', 'AUTO-PERMISSIONS', 'AUTO-KERNEL');


-- ============================================================
-- PART 4: Update severity on L1 runbooks to match actual risk
-- ============================================================

UPDATE runbooks SET severity = 'high'
WHERE runbook_id IN ('L1-LIN-FW-001', 'L1-LIN-SSH-001', 'L1-SUID-001', 'L1-LIN-SVC-001')
AND severity = 'medium';

UPDATE runbooks SET severity = 'critical'
WHERE runbook_id IN ('L1-LIN-KERN-001')
AND severity = 'medium';

COMMIT;
