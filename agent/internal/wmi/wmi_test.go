// Package wmi provides helpers for Windows Management Instrumentation queries.
package wmi

import (
	"context"
	"runtime"
	"testing"
)

func TestQueryResultPropertyHelpers(t *testing.T) {
	result := QueryResult{
		"StringProp": "value",
		"BoolProp":   true,
		"IntProp":    int32(42),
		"Int64Prop":  int64(100),
		"Uint32Prop": uint32(200),
	}

	// Test string property
	if val, ok := GetPropertyString(result, "StringProp"); !ok || val != "value" {
		t.Errorf("expected 'value', got '%s', ok=%v", val, ok)
	}

	// Test missing string property
	if _, ok := GetPropertyString(result, "Missing"); ok {
		t.Error("expected ok=false for missing property")
	}

	// Test bool property
	if val, ok := GetPropertyBool(result, "BoolProp"); !ok || !val {
		t.Errorf("expected true, got %v, ok=%v", val, ok)
	}

	// Test int32 property
	if val, ok := GetPropertyInt(result, "IntProp"); !ok || val != 42 {
		t.Errorf("expected 42, got %d, ok=%v", val, ok)
	}

	// Test int64 property
	if val, ok := GetPropertyInt(result, "Int64Prop"); !ok || val != 100 {
		t.Errorf("expected 100, got %d, ok=%v", val, ok)
	}

	// Test uint32 property
	if val, ok := GetPropertyInt(result, "Uint32Prop"); !ok || val != 200 {
		t.Errorf("expected 200, got %d, ok=%v", val, ok)
	}

	// Test missing int property
	if _, ok := GetPropertyInt(result, "Missing"); ok {
		t.Error("expected ok=false for missing property")
	}

	// Test wrong type for int
	if _, ok := GetPropertyInt(result, "StringProp"); ok {
		t.Error("expected ok=false for wrong type")
	}
}

func TestQueryOnNonWindows(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("skipping non-Windows test on Windows")
	}

	ctx := context.Background()

	// Query should fail on non-Windows
	_, err := Query(ctx, "root\\CIMV2", "SELECT * FROM Win32_ComputerSystem")
	if err == nil {
		t.Error("expected error on non-Windows platform")
	}
}

func TestQuerySingleOnNonWindows(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("skipping non-Windows test on Windows")
	}

	ctx := context.Background()

	// QuerySingle should fail on non-Windows
	_, err := QuerySingle(ctx, "root\\CIMV2", "SELECT * FROM Win32_ComputerSystem")
	if err == nil {
		t.Error("expected error on non-Windows platform")
	}
}

func TestRegistryOnNonWindows(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("skipping non-Windows test on Windows")
	}

	ctx := context.Background()

	// Registry functions should fail on non-Windows
	_, err := GetRegistryDWORD(ctx, HKEY_LOCAL_MACHINE, "SOFTWARE\\Test", "Value")
	if err == nil {
		t.Error("expected error for GetRegistryDWORD on non-Windows")
	}

	_, err = GetRegistryString(ctx, HKEY_LOCAL_MACHINE, "SOFTWARE\\Test", "Value")
	if err == nil {
		t.Error("expected error for GetRegistryString on non-Windows")
	}

	_, err = RegistryKeyExists(ctx, HKEY_LOCAL_MACHINE, "SOFTWARE\\Test")
	if err == nil {
		t.Error("expected error for RegistryKeyExists on non-Windows")
	}
}

func TestRegistryHiveConstants(t *testing.T) {
	// Verify registry hive constants match Windows values
	if HKEY_CLASSES_ROOT != 0x80000000 {
		t.Error("HKEY_CLASSES_ROOT has wrong value")
	}
	if HKEY_CURRENT_USER != 0x80000001 {
		t.Error("HKEY_CURRENT_USER has wrong value")
	}
	if HKEY_LOCAL_MACHINE != 0x80000002 {
		t.Error("HKEY_LOCAL_MACHINE has wrong value")
	}
	if HKEY_USERS != 0x80000003 {
		t.Error("HKEY_USERS has wrong value")
	}
	if HKEY_CURRENT_CONFIG != 0x80000005 {
		t.Error("HKEY_CURRENT_CONFIG has wrong value")
	}
}
