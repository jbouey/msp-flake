package daemon

import (
	"context"
	"fmt"
	"log"
	"net"
	"strings"
	"time"
)

const (
	agentStaleTimeout = 10 * time.Minute
	selfHealInterval  = 5 * time.Minute
	// Cooldown between redeploy attempts. Escalates with consecutive failures:
	// attempt 1-3: 10 min, attempt 4-6: 30 min, attempt 7+: 2 hours.
	// Never gives up — end clients restore VMs, reimage, do unpredictable things.
	redeployCooldownBase    = 10 * time.Minute
	redeployCooldownMedium  = 30 * time.Minute
	redeployCooldownBackoff = 2 * time.Hour
)

// agentHealthEntry tracks a known agent's health status.
type agentHealthEntry struct {
	Hostname        string
	IPAddress       string
	LastHeartbeat   time.Time
	DeployAttempts  int
	LastDeployAt    time.Time // when the last deploy was attempted
	LastSuccessAt   time.Time // when the last heartbeat was received (for recovery detection)
	OSType          string
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
		// Agent heartbeating recently — healthy, reset any failure counters
		if !entry.LastHeartbeat.IsZero() && time.Since(entry.LastHeartbeat) < 5*time.Minute {
			if entry.DeployAttempts > 0 {
				log.Printf("[selfheal] Agent %s recovered (heartbeating after %d deploys)", hostname, entry.DeployAttempts)
				entry.DeployAttempts = 0
			}
			entry.LastSuccessAt = entry.LastHeartbeat
			continue
		}

		// Not heartbeating — check if actually stale
		if !entry.LastHeartbeat.IsZero() && time.Since(entry.LastHeartbeat) < agentStaleTimeout {
			continue // not stale yet
		}

		// Cooldown: don't hammer with deploys. Backoff increases with failures.
		if !entry.LastDeployAt.IsZero() {
			cooldown := redeployCooldownBase
			if entry.DeployAttempts > 6 {
				cooldown = redeployCooldownBackoff
			} else if entry.DeployAttempts > 3 {
				cooldown = redeployCooldownMedium
			}
			if time.Since(entry.LastDeployAt) < cooldown {
				continue // still in cooldown
			}
		}

		staleDuration := time.Since(entry.LastHeartbeat)
		if entry.LastHeartbeat.IsZero() {
			staleDuration = agentStaleTimeout + time.Minute
		}

		log.Printf("[selfheal] Agent %s stale for %v (attempt %d), checking...",
			hostname, staleDuration.Round(time.Second), entry.DeployAttempts+1)

		// Probe: is the host reachable?
		probeAddr := entry.IPAddress
		if probeAddr == "" {
			probeAddr = sh.resolveAgentIP(hostname)
			if probeAddr == "" {
				log.Printf("[selfheal] Agent %s has no IP — skipping", hostname)
				continue
			}
			entry.IPAddress = probeAddr
			log.Printf("[selfheal] Resolved %s → %s", hostname, probeAddr)
		}
		probe := probeHost(ctx, probeAddr)
		if !probe.SSHOpen && !probe.WinRMOpen {
			log.Printf("[selfheal] Host %s (%s) unreachable — powered off or network down", hostname, probeAddr)
			continue
		}

		// Host is up but agent is silent — redeploy.
		// Never give up. Cooldown prevents flood. End users restore VMs,
		// reimage, move machines — the agent must always come back.
		entry.DeployAttempts++
		entry.LastDeployAt = time.Now()

		// Report incident on first failure and periodically (every 6 attempts)
		// so the dashboard shows the issue, but don't stop retrying.
		if entry.DeployAttempts == 1 || entry.DeployAttempts%6 == 0 {
			sh.daemon.scanner.reportDrift(&driftFinding{
				Hostname:     hostname,
				CheckType:    "AGENT-REDEPLOY-EXHAUSTED",
				Expected:     "Agent responding to heartbeat",
				Actual:       fmt.Sprintf("Agent silent for %v, %d redeploy attempts", staleDuration.Round(time.Second), entry.DeployAttempts),
				HIPAAControl: "164.312(a)(1)",
				Severity:     "high",
				Details: map[string]string{
					"hostname":        hostname,
					"ip_address":      entry.IPAddress,
					"deploy_attempts": fmt.Sprintf("%d", entry.DeployAttempts),
					"stale_duration":  staleDuration.Round(time.Second).String(),
				},
			})
		}

		// Infer OS type from probe results if not set (pre-v0.3.28 agents).
		// Check WinRM first — Windows hosts may also have SSH (from the Go agent).
		if entry.OSType == "" {
			if probe.WinRMOpen {
				entry.OSType = "windows"
				log.Printf("[selfheal] Inferred OS type 'windows' for %s (WinRM open)", hostname)
			} else if probe.SSHOpen {
				entry.OSType = "linux" // could be macOS, but SSH deploy handles both
				log.Printf("[selfheal] Inferred OS type 'linux' for %s (SSH open)", hostname)
			} else {
				log.Printf("[selfheal] Agent %s has no OS type and no open ports — skipping", hostname)
				continue
			}
		}

		log.Printf("[selfheal] Attempting redeploy #%d for %s (os=%s)", entry.DeployAttempts, hostname, entry.OSType)

		var err error
		if entry.OSType == "windows" {
			// Windows: WinRM deploy via autodeploy's fallback chain (direct → DC proxy)
			err = sh.daemon.deployer.DeployWindowsAgentByHostname(ctx, hostname, entry.IPAddress)
		} else {
			// Linux/macOS: SSH deploy
			creds := sh.daemon.findCredentialsForHost(hostname, entry.IPAddress)
			if creds == nil && entry.IPAddress != "" {
				creds = sh.daemon.findCredentialsForHost(entry.IPAddress, hostname)
			}
			if creds == nil {
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
			err = sh.daemon.deployViaSSH(ctx, deploy, sh.daemon.config.SiteID)
		}

		if err != nil {
			log.Printf("[selfheal] Redeploy failed for %s: %v", hostname, err)
		} else {
			log.Printf("[selfheal] Redeploy succeeded for %s", hostname)
			entry.DeployAttempts = 0
			// Report successful deploy so Central Command updates sensor_deployed
			sh.daemon.state.AddDeployResult(DeployResult{
				DeviceID: fmt.Sprintf("selfheal-%s", hostname),
				Hostname: hostname,
				OSType:   entry.OSType,
				Status:   "success",
			})
		}
	}
}

// resolveAgentIP tries multiple sources to find an IP for an agent hostname:
// 1. Credential store (winTargets keyed by hostname or IP)
// 2. AD computer cache from autodeploy
// 3. DNS resolution
// 4. Domain controller config (if hostname looks like a DC)
func (sh *selfHealer) resolveAgentIP(hostname string) string {
	// 1. Direct credential lookup
	if wt, ok := sh.daemon.LookupWinTarget(hostname); ok {
		return wt.Hostname
	}

	// 2. Search AD computer cache for matching hostname
	if sh.daemon.deployer != nil {
		if ip := sh.daemon.deployer.lookupADHostIP(hostname); ip != "" {
			return ip
		}
	}

	// 3. DNS resolution
	if addrs, err := net.LookupHost(hostname); err == nil && len(addrs) > 0 {
		return addrs[0]
	}

	// 3b. Try matching agent hostname against linux target labels/hostnames
	// Credentials are often keyed by IP, but the label matches the hostname
	// (e.g., label="MaCs-iMac.local", hostname="192.168.88.50")
	for _, lt := range sh.svc.Targets.GetLinuxTargets() {
		if strings.EqualFold(lt.Label, hostname) || strings.EqualFold(lt.Hostname, hostname) {
			return lt.Hostname // lt.Hostname is the IP from the credential
		}
	}

	// 4. DC config fallback — if this hostname matches the DC pattern
	if sh.svc.Config.DomainController != nil {
		dc := *sh.svc.Config.DomainController
		hn := strings.ToUpper(hostname)
		if strings.Contains(hn, "DC") || strings.HasSuffix(hn, "DC01") || strings.HasSuffix(hn, "DC02") {
			log.Printf("[selfheal] Agent %s matches DC pattern, using DC IP %s", hostname, dc)
			return dc
		}
	}

	return ""
}
