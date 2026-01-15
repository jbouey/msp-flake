// Package checks implements Windows compliance checks.
package checks

import (
	"context"
	"fmt"

	"github.com/osiriscare/agent/internal/wmi"
)

// BitLockerCheck verifies BitLocker encryption is enabled on the system drive.
//
// HIPAA Control: §164.312(a)(2)(iv) - Encryption and Decryption
// Checks: C: drive has BitLocker protection enabled
type BitLockerCheck struct{}

// Name returns the check identifier
func (c *BitLockerCheck) Name() string {
	return "bitlocker"
}

// Run executes the BitLocker compliance check
func (c *BitLockerCheck) Run(ctx context.Context) CheckResult {
	result := CheckResult{
		CheckType:    "bitlocker",
		HIPAAControl: "164.312(a)(2)(iv)",
		Metadata:     make(map[string]string),
	}

	// Query WMI for BitLocker status
	// Namespace: root\CIMV2\Security\MicrosoftVolumeEncryption
	// Class: Win32_EncryptableVolume
	volumes, err := wmi.Query(ctx,
		"root\\CIMV2\\Security\\MicrosoftVolumeEncryption",
		"SELECT DriveLetter, ProtectionStatus, EncryptionMethod FROM Win32_EncryptableVolume WHERE DriveLetter = 'C:'",
	)
	if err != nil {
		result.Error = err
		result.Passed = false
		result.Actual = fmt.Sprintf("WMI query failed: %v", err)
		result.Expected = "BitLocker enabled"
		return result
	}

	if len(volumes) == 0 {
		result.Passed = false
		result.Expected = "BitLocker enabled"
		result.Actual = "No encryptable volumes found"
		return result
	}

	vol := volumes[0]

	// Get protection status
	protectionStatus, ok := wmi.GetPropertyInt(vol, "ProtectionStatus")
	if !ok {
		result.Passed = false
		result.Expected = "ProtectionStatus=1 (On)"
		result.Actual = "Could not read ProtectionStatus"
		return result
	}

	// ProtectionStatus: 0=Off, 1=On, 2=Unknown
	if protectionStatus != 1 {
		result.Passed = false
		result.Expected = "ProtectionStatus=1 (On)"
		result.Actual = fmt.Sprintf("ProtectionStatus=%d", protectionStatus)

		if driveLetter, ok := wmi.GetPropertyString(vol, "DriveLetter"); ok {
			result.Metadata["drive_letter"] = driveLetter
		}
		if encMethod, ok := wmi.GetPropertyInt(vol, "EncryptionMethod"); ok {
			result.Metadata["encryption_method"] = fmt.Sprintf("%d", encMethod)
		}
		return result
	}

	// Add metadata about encryption method
	if encMethod, ok := wmi.GetPropertyInt(vol, "EncryptionMethod"); ok {
		result.Metadata["encryption_method"] = encryptionMethodName(encMethod)
	}

	result.Passed = true
	result.Expected = "ProtectionStatus=1 (On)"
	result.Actual = "ProtectionStatus=1 (On)"
	return result
}

// encryptionMethodName converts BitLocker encryption method code to name
func encryptionMethodName(method int) string {
	switch method {
	case 0:
		return "None"
	case 1:
		return "AES_128_WITH_DIFFUSER"
	case 2:
		return "AES_256_WITH_DIFFUSER"
	case 3:
		return "AES_128"
	case 4:
		return "AES_256"
	case 5:
		return "HARDWARE_ENCRYPTION"
	case 6:
		return "XTS_AES_128"
	case 7:
		return "XTS_AES_256"
	default:
		return fmt.Sprintf("Unknown(%d)", method)
	}
}
