//go:build windows

// Package wmi provides Windows-specific WMI query implementation.
package wmi

import (
	"context"
	"fmt"

	"github.com/go-ole/go-ole"
	"github.com/go-ole/go-ole/oleutil"
)

// queryWindows executes a WMI query on Windows using COM/OLE
func queryWindows(ctx context.Context, namespace, query string) ([]QueryResult, error) {
	// Check context before expensive COM operations
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}

	// Initialize COM
	if err := ole.CoInitializeEx(0, ole.COINIT_MULTITHREADED); err != nil {
		oleErr, ok := err.(*ole.OleError)
		// S_FALSE means already initialized, which is fine
		if !ok || oleErr.Code() != 0x00000001 {
			return nil, fmt.Errorf("COM initialization failed: %w", err)
		}
	}
	defer ole.CoUninitialize()

	// Create WMI locator
	unknown, err := oleutil.CreateObject("WbemScripting.SWbemLocator")
	if err != nil {
		return nil, fmt.Errorf("failed to create WMI locator: %w", err)
	}
	defer unknown.Release()

	wmi, err := unknown.QueryInterface(ole.IID_IDispatch)
	if err != nil {
		return nil, fmt.Errorf("failed to get IDispatch: %w", err)
	}
	defer wmi.Release()

	// Check context before connecting
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}

	// Connect to namespace
	serviceRaw, err := oleutil.CallMethod(wmi, "ConnectServer", ".", namespace)
	if err != nil {
		return nil, fmt.Errorf("failed to connect to %s: %w", namespace, err)
	}
	service := serviceRaw.ToIDispatch()
	defer service.Release()

	// Execute query
	resultRaw, err := oleutil.CallMethod(service, "ExecQuery", query)
	if err != nil {
		return nil, fmt.Errorf("query failed: %w", err)
	}
	result := resultRaw.ToIDispatch()
	defer result.Release()

	// Get count
	countRaw, err := oleutil.GetProperty(result, "Count")
	if err != nil {
		return nil, fmt.Errorf("failed to get count: %w", err)
	}
	count := int(countRaw.Val)

	// Iterate results
	results := make([]QueryResult, 0, count)

	for i := 0; i < count; i++ {
		// Check context cancellation
		select {
		case <-ctx.Done():
			return results, ctx.Err()
		default:
		}

		// Get item
		itemRaw, err := oleutil.CallMethod(result, "ItemIndex", i)
		if err != nil {
			continue
		}
		item := itemRaw.ToIDispatch()

		// Build result map by getting properties directly from the item
		// This is more reliable than iterating Properties_ collection
		qr := make(QueryResult)

		// Common Win32_Service properties we need
		propNames := []string{
			"Name", "State", "Status", "DisplayName", "StartMode",
			"PathName", "ProcessId", "ServiceType", "StartName",
			// For other WMI classes, add commonly needed properties
			"AntivirusEnabled", "RealTimeProtectionEnabled", // Defender
			"HotFixID", "InstalledOn", // Patches
		}

		for _, propName := range propNames {
			valRaw, err := oleutil.GetProperty(item, propName)
			if err != nil {
				continue // Property doesn't exist for this class
			}

			// Convert to Go type
			var val interface{}
			switch valRaw.VT {
			case ole.VT_NULL, ole.VT_EMPTY:
				val = nil
			case ole.VT_BOOL:
				val = valRaw.Val != 0
			case ole.VT_I4, ole.VT_INT:
				val = int32(valRaw.Val)
			case ole.VT_UI4, ole.VT_UINT:
				val = uint32(valRaw.Val)
			case ole.VT_BSTR:
				val = valRaw.ToString()
			default:
				val = valRaw.Value()
			}

			// Free VARIANT resources (BSTR strings, COM references)
			valRaw.Clear()

			qr[propName] = val
		}

		results = append(results, qr)
		item.Release()
	}

	return results, nil
}

// getRegistryDWORDWindows reads a DWORD registry value using WMI StdRegProv
func getRegistryDWORDWindows(ctx context.Context, hive uint32, subKey, valueName string) (uint32, error) {
	if ctx.Err() != nil {
		return 0, ctx.Err()
	}
	// Initialize COM
	if err := ole.CoInitializeEx(0, ole.COINIT_MULTITHREADED); err != nil {
		oleErr, ok := err.(*ole.OleError)
		if !ok || oleErr.Code() != 0x00000001 {
			return 0, fmt.Errorf("COM initialization failed: %w", err)
		}
	}
	defer ole.CoUninitialize()

	// Create WMI locator
	unknown, err := oleutil.CreateObject("WbemScripting.SWbemLocator")
	if err != nil {
		return 0, fmt.Errorf("failed to create WMI locator: %w", err)
	}
	defer unknown.Release()

	wmi, err := unknown.QueryInterface(ole.IID_IDispatch)
	if err != nil {
		return 0, fmt.Errorf("failed to get IDispatch: %w", err)
	}
	defer wmi.Release()

	// Connect to default namespace for StdRegProv
	serviceRaw, err := oleutil.CallMethod(wmi, "ConnectServer", ".", "root\\default")
	if err != nil {
		return 0, fmt.Errorf("failed to connect to root\\default: %w", err)
	}
	service := serviceRaw.ToIDispatch()
	defer service.Release()

	// Get StdRegProv class
	regRaw, err := oleutil.CallMethod(service, "Get", "StdRegProv")
	if err != nil {
		return 0, fmt.Errorf("failed to get StdRegProv: %w", err)
	}
	reg := regRaw.ToIDispatch()
	defer reg.Release()

	// Create output parameters
	outParams, err := oleutil.CallMethod(reg, "GetDWORDValue", hive, subKey, valueName)
	if err != nil {
		return 0, fmt.Errorf("GetDWORDValue failed: %w", err)
	}

	// Parse result - GetDWORDValue returns result in "uValue" output parameter
	result := outParams.ToIDispatch()
	defer result.Release()

	valueRaw, err := oleutil.GetProperty(result, "uValue")
	if err != nil {
		return 0, fmt.Errorf("failed to get uValue: %w", err)
	}

	return uint32(valueRaw.Val), nil
}

// getRegistryStringWindows reads a string registry value using WMI StdRegProv
func getRegistryStringWindows(ctx context.Context, hive uint32, subKey, valueName string) (string, error) {
	if ctx.Err() != nil {
		return "", ctx.Err()
	}
	// Initialize COM
	if err := ole.CoInitializeEx(0, ole.COINIT_MULTITHREADED); err != nil {
		oleErr, ok := err.(*ole.OleError)
		if !ok || oleErr.Code() != 0x00000001 {
			return "", fmt.Errorf("COM initialization failed: %w", err)
		}
	}
	defer ole.CoUninitialize()

	// Create WMI locator
	unknown, err := oleutil.CreateObject("WbemScripting.SWbemLocator")
	if err != nil {
		return "", fmt.Errorf("failed to create WMI locator: %w", err)
	}
	defer unknown.Release()

	wmi, err := unknown.QueryInterface(ole.IID_IDispatch)
	if err != nil {
		return "", fmt.Errorf("failed to get IDispatch: %w", err)
	}
	defer wmi.Release()

	// Connect to default namespace for StdRegProv
	serviceRaw, err := oleutil.CallMethod(wmi, "ConnectServer", ".", "root\\default")
	if err != nil {
		return "", fmt.Errorf("failed to connect to root\\default: %w", err)
	}
	service := serviceRaw.ToIDispatch()
	defer service.Release()

	// Get StdRegProv class
	regRaw, err := oleutil.CallMethod(service, "Get", "StdRegProv")
	if err != nil {
		return "", fmt.Errorf("failed to get StdRegProv: %w", err)
	}
	reg := regRaw.ToIDispatch()
	defer reg.Release()

	// Call GetStringValue
	outParams, err := oleutil.CallMethod(reg, "GetStringValue", hive, subKey, valueName)
	if err != nil {
		return "", fmt.Errorf("GetStringValue failed: %w", err)
	}

	// Parse result
	result := outParams.ToIDispatch()
	defer result.Release()

	valueRaw, err := oleutil.GetProperty(result, "sValue")
	if err != nil {
		return "", fmt.Errorf("failed to get sValue: %w", err)
	}

	return valueRaw.ToString(), nil
}

// registryKeyExistsWindows checks if a registry key exists using WMI StdRegProv
func registryKeyExistsWindows(ctx context.Context, hive uint32, subKey string) (bool, error) {
	if ctx.Err() != nil {
		return false, ctx.Err()
	}
	// Initialize COM
	if err := ole.CoInitializeEx(0, ole.COINIT_MULTITHREADED); err != nil {
		oleErr, ok := err.(*ole.OleError)
		if !ok || oleErr.Code() != 0x00000001 {
			return false, fmt.Errorf("COM initialization failed: %w", err)
		}
	}
	defer ole.CoUninitialize()

	// Create WMI locator
	unknown, err := oleutil.CreateObject("WbemScripting.SWbemLocator")
	if err != nil {
		return false, fmt.Errorf("failed to create WMI locator: %w", err)
	}
	defer unknown.Release()

	wmi, err := unknown.QueryInterface(ole.IID_IDispatch)
	if err != nil {
		return false, fmt.Errorf("failed to get IDispatch: %w", err)
	}
	defer wmi.Release()

	// Connect to default namespace for StdRegProv
	serviceRaw, err := oleutil.CallMethod(wmi, "ConnectServer", ".", "root\\default")
	if err != nil {
		return false, fmt.Errorf("failed to connect to root\\default: %w", err)
	}
	service := serviceRaw.ToIDispatch()
	defer service.Release()

	// Get StdRegProv class
	regRaw, err := oleutil.CallMethod(service, "Get", "StdRegProv")
	if err != nil {
		return false, fmt.Errorf("failed to get StdRegProv: %w", err)
	}
	reg := regRaw.ToIDispatch()
	defer reg.Release()

	// Call EnumKey and check ReturnValue (0=exists, 2=not found, 5=access denied)
	outParams, err := oleutil.CallMethod(reg, "EnumKey", hive, subKey)
	if err != nil {
		return false, fmt.Errorf("EnumKey COM call failed: %w", err)
	}

	out := outParams.ToIDispatch()
	defer out.Release()

	retValRaw, err := oleutil.GetProperty(out, "ReturnValue")
	if err != nil {
		return false, fmt.Errorf("failed to get EnumKey ReturnValue: %w", err)
	}

	retVal := int(retValRaw.Val)
	retValRaw.Clear()

	switch retVal {
	case 0:
		return true, nil
	case 2:
		return false, nil // Key not found
	default:
		return false, fmt.Errorf("EnumKey returned error code %d for key %s", retVal, subKey)
	}
}

