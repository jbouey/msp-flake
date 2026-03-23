package daemon

import "testing"

func TestLifecycleTransitions(t *testing.T) {
	tests := []struct {
		name    string
		current string
		event   string
		want    string
	}{
		{"discovered to probed", "discovered", "probed", "probed"},
		{"probed to ad_managed", "probed", "ad_joined", "ad_managed"},
		{"probed to take_over", "probed", "ssh_open", "take_over_available"},
		{"ad_managed to deploying", "ad_managed", "deploy_start", "deploying"},
		{"take_over to pending", "take_over_available", "creds_saved", "pending_deploy"},
		{"pending to deploying", "pending_deploy", "deploy_start", "deploying"},
		{"deploying to active", "deploying", "agent_heartbeat", "agent_active"},
		{"deploying to failed", "deploying", "deploy_error", "deploy_failed"},
		{"active to stale", "agent_active", "heartbeat_timeout", "agent_stale"},
		{"stale to active", "agent_stale", "agent_heartbeat", "agent_active"},
		{"stale to offline", "agent_stale", "offline_7d", "agent_offline"},
		{"offline to archived", "agent_offline", "archive_30d", "archived"},
		{"archived reappears", "archived", "seen", "discovered"},
		{"ignored stays ignored", "ignored", "probed", "ignored"},
		{"ignored can be unignored", "ignored", "unignore", "discovered"},
		{"failed can retry", "deploy_failed", "deploy_start", "deploying"},
		{"no transition = stay", "agent_active", "probed", "agent_active"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := nextState(tt.current, tt.event)
			if got != tt.want {
				t.Errorf("nextState(%q, %q) = %q, want %q", tt.current, tt.event, got, tt.want)
			}
		})
	}
}
