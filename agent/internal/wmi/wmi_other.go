//go:build !windows

// Package wmi provides stubs for non-Windows platforms.
package wmi

import (
	"context"
	"fmt"
)

// queryWindows is a stub for non-Windows platforms
func queryWindows(ctx context.Context, namespace, query string) ([]QueryResult, error) {
	return nil, fmt.Errorf("WMI queries only supported on Windows")
}

// getRegistryDWORDWindows is a stub for non-Windows platforms
func getRegistryDWORDWindows(ctx context.Context, hive uint32, subKey, valueName string) (uint32, error) {
	return 0, fmt.Errorf("registry queries only supported on Windows")
}

// getRegistryStringWindows is a stub for non-Windows platforms
func getRegistryStringWindows(ctx context.Context, hive uint32, subKey, valueName string) (string, error) {
	return "", fmt.Errorf("registry queries only supported on Windows")
}

// registryKeyExistsWindows is a stub for non-Windows platforms
func registryKeyExistsWindows(ctx context.Context, hive uint32, subKey string) (bool, error) {
	return false, fmt.Errorf("registry queries only supported on Windows")
}
