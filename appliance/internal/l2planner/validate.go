package l2planner

import (
	"fmt"
	"strings"

	"github.com/osiriscare/appliance/internal/l2bridge"
)

// ValidateDecision checks that an LLM decision references real resources
// and stays within authorized scope. Returns error if invalid.
func ValidateDecision(d *l2bridge.LLMDecision, knownRunbooks map[string]bool, knownHosts []string) error {
	// Action must be non-empty
	if d.RecommendedAction == "" {
		return fmt.Errorf("empty recommended_action")
	}

	// If action is execute_runbook or run_*, runbook_id must exist in registry
	if d.RecommendedAction == "execute_runbook" || strings.HasPrefix(d.RecommendedAction, "run_") {
		if d.RunbookID == "" {
			return fmt.Errorf("execute_runbook action without runbook_id")
		}
		if knownRunbooks != nil && !knownRunbooks[d.RunbookID] {
			return fmt.Errorf("unknown runbook_id: %s", d.RunbookID)
		}
	}

	// If action targets a host, host must be known
	if d.ActionParams != nil {
		if hostID, ok := d.ActionParams["host_id"].(string); ok && hostID != "" {
			found := false
			for _, h := range knownHosts {
				if strings.EqualFold(h, hostID) {
					found = true
					break
				}
			}
			if !found && len(knownHosts) > 0 {
				return fmt.Errorf("unknown target host: %s", hostID)
			}
		}
	}

	// Confidence must be in [0, 1]
	if d.Confidence < 0 || d.Confidence > 1 {
		return fmt.Errorf("confidence out of range: %f", d.Confidence)
	}

	return nil
}
