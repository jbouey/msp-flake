//go:build darwin

package healing

import (
	"context"
	"testing"

	pb "github.com/osiriscare/agent/proto"
)

func TestMacExecute_DispatchesKnownCheckTypes(t *testing.T) {
	knownTypes := []string{
		"macos_firewall",
		"macos_auto_update",
		"macos_screen_lock",
		"macos_ntp_sync",
		"macos_file_sharing",
		"macos_gatekeeper",
		"macos_filevault",
		"macos_time_machine",
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
			// Should NOT be the generic "manual remediation" fallback
			if res.Error == "check type "+ct+" requires manual remediation" {
				t.Errorf("check type %s fell through to default case", ct)
			}
		})
	}
}

func TestMacExecute_UnknownCheckType(t *testing.T) {
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
}

func TestMacRunShell_BasicExecution(t *testing.T) {
	out, err := runShell(context.Background(), "echo hello")
	if err != nil {
		t.Fatalf("runShell failed: %v", err)
	}
	if out != "hello" {
		t.Errorf("got %q, want %q", out, "hello")
	}
}

func TestMacRunShell_FailingCommand(t *testing.T) {
	_, err := runShell(context.Background(), "exit 1")
	if err == nil {
		t.Error("expected error from failing command")
	}
}

func TestMacRunShell_ContextCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := runShell(ctx, "sleep 10")
	if err == nil {
		t.Error("expected error from cancelled context")
	}
}
