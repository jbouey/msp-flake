//go:build !windows

package discovery

// DiscoverDomain returns empty on non-Windows (no AD).
func DiscoverDomain() string {
	return ""
}
