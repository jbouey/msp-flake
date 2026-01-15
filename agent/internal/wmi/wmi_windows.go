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

		// Get properties
		propsRaw, err := oleutil.GetProperty(item, "Properties_")
		if err != nil {
			item.Release()
			continue
		}
		props := propsRaw.ToIDispatch()

		// Get property count
		propCountRaw, err := oleutil.GetProperty(props, "Count")
		if err != nil {
			props.Release()
			item.Release()
			continue
		}
		propCount := int(propCountRaw.Val)

		// Build result map
		qr := make(QueryResult)

		for j := 0; j < propCount; j++ {
			propRaw, err := oleutil.CallMethod(props, "ItemIndex", j)
			if err != nil {
				continue
			}
			prop := propRaw.ToIDispatch()

			// Get property name
			nameRaw, err := oleutil.GetProperty(prop, "Name")
			if err != nil {
				prop.Release()
				continue
			}
			name := nameRaw.ToString()

			// Get property value
			valRaw, err := oleutil.GetProperty(prop, "Value")
			if err != nil {
				prop.Release()
				continue
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

			qr[name] = val
			prop.Release()
		}

		results = append(results, qr)

		props.Release()
		item.Release()
	}

	return results, nil
}
