package daemon

import (
	"context"

	"github.com/osiriscare/appliance/internal/grpcserver"
	"github.com/osiriscare/appliance/internal/sshexec"
	"github.com/osiriscare/appliance/internal/winrm"
)

// TargetProvider gives subsystems access to credential and target information
// without coupling them to the full Daemon.
type TargetProvider interface {
	LookupWinTarget(hostname string) (winTarget, bool)
	GetLinuxTargets() []linuxTarget
	FindCredentialsForHost(hostname, ip string) *HostCredentials
	ProbeWinRMPort(hostname string) winrmSettings
	GetWinTargets() map[string]winTarget
	GetADHostnames() map[string]bool
}

// CooldownManager handles drift report deduplication.
type CooldownManager interface {
	ShouldSuppress(key string) bool
}

// CheckConfig provides site-specific check configuration.
type CheckConfig interface {
	IsDisabled(checkType string) bool
	GetDisabledChecks() map[string]bool
}

// IncidentSink receives drift incident reports.
type IncidentSink interface {
	ReportDriftIncident(hostname, checkType, expected, actual, hipaaControl, severity, platform string)
	ReportHealed(hostname, checkType, resolutionTier, ruleID string)
}

// Services bundles the interfaces and shared executors that subsystems need.
// Subsystems receive *Services instead of *Daemon, breaking the circular dependency.
type Services struct {
	Config    *Config
	Targets   TargetProvider
	Cooldowns CooldownManager
	Checks    CheckConfig
	Incidents IncidentSink
	WinRM     *winrm.Executor
	SSH       *sshexec.Executor
	Registry  *grpcserver.AgentRegistry
	RunCtx    context.Context
	SiteID    string
	Mesh      *Mesh
}
