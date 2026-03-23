package daemon

import (
	"context"
	"fmt"
	"log"
	"time"
)

const (
	agentStaleTimeout   = 10 * time.Minute
	maxRedeployAttempts = 3
	selfHealInterval    = 5 * time.Minute
)

// agentHealthEntry tracks a known agent's health status.
type agentHealthEntry struct {
	Hostname       string
	IPAddress      string
	LastHeartbeat  time.Time
	DeployAttempts int
	Escalated      bool
	OSType         string
}

// selfHealer monitors agent heartbeats and auto-redeploys dead agents.
type selfHealer struct {
	svc     *Services // interfaces for decoupled access
	daemon  *Daemon   // for deploy methods, scanner, credentials, etc.
	agents  map[string]*agentHealthEntry // keyed by hostname
	lastRun time.Time
}

func newSelfHealer(svc *Services, d *Daemon) *selfHealer {
	return &selfHealer{
		svc:    svc,
		daemon: d,
		agents: make(map[string]*agentHealthEntry),
	}
}

// GoAgentInfo is a lightweight struct for agent data from the gRPC registry.
type GoAgentInfo struct {
	Hostname      string
	IPAddress     string
	OSType        string
	LastHeartbeat time.Time
	Status        string
}

// updateFromCheckin refreshes agent health data from the registry snapshot.
// Call this after each checkin cycle to keep heartbeat timestamps current.
func (sh *selfHealer) updateFromCheckin(agentData []GoAgentInfo) {
	for _, a := range agentData {
		entry, exists := sh.agents[a.Hostname]
		if !exists {
			entry = &agentHealthEntry{
				Hostname:  a.Hostname,
				IPAddress: a.IPAddress,
				OSType:    a.OSType,
			}
			sh.agents[a.Hostname] = entry
		}
		entry.LastHeartbeat = a.LastHeartbeat
		// Update IP in case it changed via DHCP
		if a.IPAddress != "" {
			entry.IPAddress = a.IPAddress
		}
	}
}

// runSelfHealIfNeeded checks for stale agents and attempts recovery.
// Runs at most once per selfHealInterval to avoid thundering-herd behaviour.
func (sh *selfHealer) runSelfHealIfNeeded(ctx context.Context) {
	if time.Since(sh.lastRun) < selfHealInterval {
		return
	}
	sh.lastRun = time.Now()

	for hostname, entry := range sh.agents {
		if entry.Escalated {
			continue // already escalated, don't retry
		}

		// Guard against zero-time heartbeats (never heartbeated)
		if entry.LastHeartbeat.IsZero() {
			log.Printf("[selfheal] Agent %s has never heartbeated — treating as stale", hostname)
		} else {
			staleDuration := time.Since(entry.LastHeartbeat)
			if staleDuration < agentStaleTimeout {
				continue // agent is healthy
			}
		}
		staleDuration := time.Since(entry.LastHeartbeat)
		if entry.LastHeartbeat.IsZero() {
			staleDuration = agentStaleTimeout + time.Minute // treat as just past threshold
		}

		log.Printf("[selfheal] Agent %s stale for %v, checking...", hostname, staleDuration.Round(time.Second))

		// Probe: is the host reachable at all?
		probe := probeHost(ctx, entry.IPAddress)
		if !probe.SSHOpen && !probe.WinRMOpen {
			log.Printf("[selfheal] Host %s unreachable — skipping (host may be powered off)", hostname)
			continue
		}

		// Host is up but agent is silent — attempt redeploy.
		entry.DeployAttempts++
		if entry.DeployAttempts > maxRedeployAttempts {
			log.Printf("[selfheal] Agent %s failed %d redeploys — escalating to L3", hostname, entry.DeployAttempts)
			entry.Escalated = true
			sh.daemon.scanner.reportDrift(&driftFinding{
				Hostname:     hostname,
				CheckType:    "AGENT-REDEPLOY-EXHAUSTED",
				Expected:     "Agent responding to heartbeat",
				Actual:       fmt.Sprintf("Agent silent for %v, %d redeploy attempts failed", staleDuration.Round(time.Second), entry.DeployAttempts),
				HIPAAControl: "164.312(a)(1)",
				Severity:     "high",
				Details: map[string]string{
					"hostname":        hostname,
					"ip_address":      entry.IPAddress,
					"deploy_attempts": fmt.Sprintf("%d", entry.DeployAttempts),
					"stale_duration":  staleDuration.Round(time.Second).String(),
				},
			})
			continue
		}

		log.Printf("[selfheal] Attempting redeploy #%d for %s", entry.DeployAttempts, hostname)

		// Look up stored credentials for this host.
		// Try hostname, then IP, then case-insensitive hostname match,
		// then fall back to domain_admin credentials (can reach any domain host).
		creds := sh.daemon.findCredentialsForHost(hostname, entry.IPAddress)
		if creds == nil && entry.IPAddress != "" {
			// Try reverse: maybe credentials are stored by IP
			creds = sh.daemon.findCredentialsForHost(entry.IPAddress, hostname)
		}
		if creds == nil {
			// Fall back to domain_admin credentials (works for any domain-joined host)
			if sh.svc.Config.DCUsername != nil && sh.svc.Config.DCPassword != nil {
				creds = &HostCredentials{
					Username: *sh.svc.Config.DCUsername,
					Password: *sh.svc.Config.DCPassword,
				}
				log.Printf("[selfheal] Using domain admin credentials for %s redeploy", hostname)
			}
		}
		if creds == nil {
			log.Printf("[selfheal] No credentials for %s (tried hostname, IP %s, domain admin) — cannot redeploy", hostname, entry.IPAddress)
			continue
		}

		deploy := PendingDeploy{
			DeviceID:     fmt.Sprintf("selfheal-%s", hostname),
			IPAddress:    entry.IPAddress,
			Hostname:     hostname,
			OSType:       entry.OSType,
			DeployMethod: "ssh",
			Username:     creds.Username,
			Password:     creds.Password,
			SSHKey:       creds.SSHKey,
		}

		err := sh.daemon.deployViaSSH(ctx, deploy, sh.daemon.config.SiteID)
		if err != nil {
			log.Printf("[selfheal] Redeploy failed for %s: %v", hostname, err)
		} else {
			log.Printf("[selfheal] Redeploy succeeded for %s", hostname)
			entry.DeployAttempts = 0 // reset counter on success
		}
	}
}
