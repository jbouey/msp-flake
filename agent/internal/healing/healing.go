// Package healing implements local remediation for HealCommands received from the appliance.
package healing

// Result holds the outcome of a heal command execution.
type Result struct {
	CommandID string
	CheckType string
	Success   bool
	Error     string
	Artifacts map[string]string
}
