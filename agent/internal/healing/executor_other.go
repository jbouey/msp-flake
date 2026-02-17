//go:build !windows

package healing

import (
	"context"
	"fmt"

	pb "github.com/osiriscare/agent/proto"
)

// Execute on non-Windows platforms always returns an error.
// Heal commands require PowerShell which is only available on Windows.
func Execute(ctx context.Context, cmd *pb.HealCommand) *Result {
	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   false,
		Error:     fmt.Sprintf("healing not supported on this platform (requires Windows)"),
	}
}
