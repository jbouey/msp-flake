//go:build linux

package healing

import (
	"context"
	"testing"

	pb "github.com/osiriscare/agent/proto"
)

func TestExecute_DispatchesKnownCheckTypes(t *testing.T) {
	knownTypes := []string{
		"linux_ssh_config",
		"linux_firewall",
		"linux_unattended_upgrades",
		"linux_suid_binaries",
		"linux_audit_logging",
		"linux_user_accounts",
		"linux_ntp_sync",
	}
	for _, ct := range knownTypes {
		t.Run(ct, func(t *testing.T) {
			cmd := &pb.HealCommand{
				CommandId:      "test-" + ct,
				CheckType:      ct,
				Action:         "heal",
				TimeoutSeconds: 5,
			}
			res := Execute(context.Background(), cmd)
			if res.CommandID != cmd.CommandId {
				t.Errorf("CommandID: got %s, want %s", res.CommandID, cmd.CommandId)
			}
			if res.CheckType != ct {
				t.Errorf("CheckType: got %s, want %s", res.CheckType, ct)
			}
			// Should NOT be the "unsupported" fallback message
			if res.Error == "check type "+ct+" requires manual remediation on Linux" {
				t.Errorf("check type %s fell through to default case", ct)
			}
		})
	}
}

func TestExecute_UnknownCheckType(t *testing.T) {
	cmd := &pb.HealCommand{
		CommandId:      "test-unknown",
		CheckType:      "nonexistent_check",
		Action:         "heal",
		TimeoutSeconds: 5,
	}
	res := Execute(context.Background(), cmd)
	if res.Success {
		t.Error("expected failure for unknown check type")
	}
	if res.Error == "" {
		t.Error("expected error message for unknown check type")
	}
}

func TestExecute_DefaultTimeout(t *testing.T) {
	cmd := &pb.HealCommand{
		CommandId:      "test-timeout",
		CheckType:      "linux_ntp_sync",
		Action:         "heal",
		TimeoutSeconds: 0,
	}
	res := Execute(context.Background(), cmd)
	if res.CommandID != "test-timeout" {
		t.Errorf("CommandID: got %s, want test-timeout", res.CommandID)
	}
}

func TestExecute_NegativeTimeout(t *testing.T) {
	cmd := &pb.HealCommand{
		CommandId:      "test-neg",
		CheckType:      "linux_ntp_sync",
		Action:         "heal",
		TimeoutSeconds: -1,
	}
	res := Execute(context.Background(), cmd)
	if res.CommandID != "test-neg" {
		t.Errorf("CommandID: got %s, want test-neg", res.CommandID)
	}
}

func TestExecute_ExcessiveTimeout(t *testing.T) {
	cmd := &pb.HealCommand{
		CommandId:      "test-excess",
		CheckType:      "linux_ntp_sync",
		Action:         "heal",
		TimeoutSeconds: 9999,
	}
	res := Execute(context.Background(), cmd)
	if res.CommandID != "test-excess" {
		t.Errorf("CommandID: got %s, want test-excess", res.CommandID)
	}
}

func TestHealLinuxUsers_AlwaysEscalates(t *testing.T) {
	cmd := &pb.HealCommand{
		CommandId:      "test-users",
		CheckType:      "linux_user_accounts",
		Action:         "heal",
		TimeoutSeconds: 5,
	}
	res := Execute(context.Background(), cmd)
	if res.Success {
		t.Error("user account healing should always escalate (fail)")
	}
	if res.Error == "" {
		t.Error("expected escalation error message")
	}
}

func TestRunShell_BasicExecution(t *testing.T) {
	out, err := runShell(context.Background(), "echo hello")
	if err != nil {
		t.Fatalf("runShell failed: %v", err)
	}
	if out != "hello" {
		t.Errorf("got %q, want %q", out, "hello")
	}
}

func TestRunShell_FailingCommand(t *testing.T) {
	_, err := runShell(context.Background(), "exit 1")
	if err == nil {
		t.Error("expected error from failing command")
	}
}

func TestRunShell_ContextCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := runShell(ctx, "sleep 10")
	if err == nil {
		t.Error("expected error from cancelled context")
	}
}
