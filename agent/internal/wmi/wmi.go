// Package wmi provides helpers for Windows Management Instrumentation queries.
//
// This package uses the go-ole library to execute WMI queries on Windows.
// On non-Windows platforms, it returns empty results without errors.
package wmi

import (
	"context"
	"fmt"
	"runtime"
)

// QueryResult represents a single WMI object result as a map of property names to values
type QueryResult map[string]interface{}

// Query executes a WMI query and returns the results.
//
// namespace: WMI namespace (e.g., "root\\CIMV2", "root\\Microsoft\\Windows\\Defender")
// query: WQL query string (e.g., "SELECT * FROM Win32_ComputerSystem")
//
// Returns a slice of QueryResult maps, one per returned WMI object.
func Query(ctx context.Context, namespace, query string) ([]QueryResult, error) {
	if runtime.GOOS != "windows" {
		return nil, fmt.Errorf("WMI queries only supported on Windows")
	}

	return queryWindows(ctx, namespace, query)
}

// QuerySingle executes a WMI query expecting a single result.
// Returns the first result or an error if no results found.
func QuerySingle(ctx context.Context, namespace, query string) (QueryResult, error) {
	results, err := Query(ctx, namespace, query)
	if err != nil {
		return nil, err
	}
	if len(results) == 0 {
		return nil, fmt.Errorf("no results for query")
	}
	return results[0], nil
}

// GetPropertyBool extracts a boolean property from a QueryResult
func GetPropertyBool(result QueryResult, name string) (bool, bool) {
	val, ok := result[name]
	if !ok {
		return false, false
	}
	bval, ok := val.(bool)
	return bval, ok
}

// GetPropertyInt extracts an integer property from a QueryResult
func GetPropertyInt(result QueryResult, name string) (int, bool) {
	val, ok := result[name]
	if !ok {
		return 0, false
	}
	switch v := val.(type) {
	case int:
		return v, true
	case int32:
		return int(v), true
	case int64:
		return int(v), true
	case uint32:
		return int(v), true
	default:
		return 0, false
	}
}

// GetPropertyString extracts a string property from a QueryResult
func GetPropertyString(result QueryResult, name string) (string, bool) {
	val, ok := result[name]
	if !ok {
		return "", false
	}
	sval, ok := val.(string)
	return sval, ok
}
