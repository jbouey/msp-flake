//go:build !windows && !darwin

package checks

// registerPlatformChecks is a no-op on unsupported platforms.
func registerPlatformChecks(r *Registry) {}

// DefaultEnabledChecks returns an empty set on unsupported platforms.
func DefaultEnabledChecks() []string {
	return nil
}
