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
