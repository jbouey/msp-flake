//go:build !windows && !darwin && !linux

package healing

import (
	"context"

	pb "github.com/osiriscare/agent/proto"
)

// Execute on unsupported platforms always returns an error.
func Execute(ctx context.Context, cmd *pb.HealCommand) *Result {
	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   false,
		Error:     "healing not supported on this platform",
	}
}
