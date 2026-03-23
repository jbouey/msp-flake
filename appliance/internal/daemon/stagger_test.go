package daemon

import (
	"testing"
)

func TestSortDeploysByPriority(t *testing.T) {
	deploys := []PendingDeploy{
		{Hostname: "workstation-01"},
		{Hostname: "NVSRV01"},
		{Hostname: "random-linux"},
		{Hostname: "NVDC01"},
	}
	sortDeploysByPriority(deploys)
	// Servers (srv, dc) should be first
	if deploys[0].Hostname != "NVSRV01" && deploys[0].Hostname != "NVDC01" {
		t.Errorf("expected server first, got %s", deploys[0].Hostname)
	}
}

func TestDeployPriority(t *testing.T) {
	tests := []struct {
		hostname string
		want     int
	}{
		{"NVSRV01", 0},
		{"NVDC01", 0},
		{"production-server", 0},
		{"workstation-01", 1},
		{"northvalley-linux", 1},
	}
	for _, tt := range tests {
		got := deployPriority(PendingDeploy{Hostname: tt.hostname})
		if got != tt.want {
			t.Errorf("deployPriority(%q) = %d, want %d", tt.hostname, got, tt.want)
		}
	}
}
