//go:build darwin

package checks

import (
	"context"
	"os/exec"
	"strings"
)

// runCmd executes a command and returns trimmed stdout.
func runCmd(ctx context.Context, name string, args ...string) (string, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	out, err := cmd.CombinedOutput()
	return strings.TrimSpace(string(out)), err
}
