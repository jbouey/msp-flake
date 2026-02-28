//go:build !windows

package updater

import "fmt"

// restartService is a no-op on non-Windows platforms.
func restartService(serviceName, dataDir string) error {
	return fmt.Errorf("self-update restart not supported on this platform")
}
