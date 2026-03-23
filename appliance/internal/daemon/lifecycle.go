package daemon

// nextState returns the next device_status given current state and event.
// If no valid transition exists, returns the current state unchanged.
func nextState(current, event string) string {
	// Ignored devices stay ignored unless explicitly un-ignored
	if current == "ignored" && event != "unignore" {
		return "ignored"
	}
	// Archived devices revert to discovered on reappearance
	if current == "archived" && event == "seen" {
		return "discovered"
	}

	transitions := map[string]map[string]string{
		"discovered":          {"probed": "probed"},
		"probed": {
			"ad_joined":  "ad_managed",
			"ssh_open":   "take_over_available",
			"winrm_open": "take_over_available",
		},
		"ad_managed":          {"deploy_start": "deploying"},
		"take_over_available": {"creds_saved": "pending_deploy"},
		"pending_deploy":      {"deploy_start": "deploying"},
		"deploying": {
			"agent_heartbeat": "agent_active",
			"deploy_error":    "deploy_failed",
		},
		"deploy_failed":  {"deploy_start": "deploying", "creds_saved": "pending_deploy"},
		"agent_active":   {"heartbeat_timeout": "agent_stale"},
		"agent_stale":    {"agent_heartbeat": "agent_active", "offline_7d": "agent_offline"},
		"agent_offline":  {"agent_heartbeat": "agent_active", "archive_30d": "archived"},
		"ignored":        {"unignore": "discovered"},
	}

	if states, ok := transitions[current]; ok {
		if next, ok := states[event]; ok {
			return next
		}
	}
	return current // no valid transition, stay in current state
}
