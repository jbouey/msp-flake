package daemon

import "testing"

func TestPreFlightResult_DefaultPassed(t *testing.T) {
	r := PreFlightResult{Passed: true}
	if !r.Passed {
		t.Error("default result should pass")
	}
}

func TestPreFlightResult_BlockerFailsCheck(t *testing.T) {
	r := PreFlightResult{Passed: true}
	r.Passed = false
	r.Blockers = append(r.Blockers, "SSH not reachable")
	if r.Passed {
		t.Error("should fail with blockers")
	}
	if len(r.Blockers) != 1 {
		t.Errorf("expected 1 blocker, got %d", len(r.Blockers))
	}
}

func TestPreFlightResult_WarningStillPasses(t *testing.T) {
	r := PreFlightResult{Passed: true}
	r.Warnings = append(r.Warnings, "RMM detected: connectwise")
	if !r.Passed {
		t.Error("warnings should not fail the check")
	}
}

func TestBuildSSHTargetFromDeploy(t *testing.T) {
	deploy := PendingDeploy{
		IPAddress: "192.168.1.1",
		Username:  "admin",
		Password:  "secret",
	}
	target := buildSSHTarget(deploy)
	if target.Hostname != "192.168.1.1" {
		t.Errorf("expected 192.168.1.1, got %s", target.Hostname)
	}
	if target.Port != 22 {
		t.Errorf("expected port 22, got %d", target.Port)
	}
	if target.Username != "admin" {
		t.Errorf("expected admin, got %s", target.Username)
	}
	if target.Password == nil || *target.Password != "secret" {
		t.Error("password not set correctly")
	}
}

func TestBuildSSHTargetFromDeploy_EmptyPassword(t *testing.T) {
	deploy := PendingDeploy{
		IPAddress: "192.168.1.1",
		Username:  "admin",
		SSHKey:    "-----BEGIN OPENSSH PRIVATE KEY-----",
	}
	target := buildSSHTarget(deploy)
	if target.Password != nil {
		t.Error("empty password should be nil")
	}
	if target.PrivateKey == nil {
		t.Error("SSH key should be set")
	}
}
