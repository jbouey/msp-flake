// Package checks implements Windows compliance checks.
package checks

import (
	"context"
	"fmt"
	"os"
	"strings"

	"github.com/osiriscare/agent/internal/wmi"
)

// RMMAgent represents a detected RMM agent
type RMMAgent struct {
	Name        string
	Version     string
	InstallPath string
	Running     bool
	DetectBy    string // "service", "process", or "path"
}

// rmmSignature defines detection criteria for an RMM product
type rmmSignature struct {
	serviceName  string
	processName  string
	installPaths []string
}

// knownRMM maps RMM product names to their detection signatures
var knownRMM = map[string]rmmSignature{
	"Datto RMM": {
		serviceName:  "CagService",
		processName:  "AEMAgent.exe",
		installPaths: []string{
			"C:\\Program Files (x86)\\CentraStage",
			"C:\\Program Files\\CentraStage",
		},
	},
	"ConnectWise Automate": {
		serviceName:  "LTService",
		processName:  "LTSvc.exe",
		installPaths: []string{
			"C:\\Windows\\LTSvc",
		},
	},
	"ConnectWise Control": {
		serviceName:  "ScreenConnect Client",
		processName:  "ScreenConnect.ClientService.exe",
		installPaths: []string{
			"C:\\Program Files (x86)\\ScreenConnect Client",
		},
	},
	"NinjaRMM": {
		serviceName:  "NinjaRMMAgent",
		processName:  "NinjaRMMAgent.exe",
		installPaths: []string{
			"C:\\ProgramData\\NinjaRMMAgent",
		},
	},
	"Kaseya VSA": {
		serviceName:  "Kaseya Agent",
		processName:  "AgentMon.exe",
		installPaths: []string{
			"C:\\Program Files (x86)\\Kaseya",
		},
	},
	"Syncro": {
		serviceName:  "Syncro",
		processName:  "syncro.exe",
		installPaths: []string{
			"C:\\ProgramData\\Syncro",
		},
	},
	"Atera": {
		serviceName:  "AteraAgent",
		processName:  "AteraAgent.exe",
		installPaths: []string{
			"C:\\Program Files\\ATERA Networks",
		},
	},
	"N-able N-central": {
		serviceName:  "Windows Agent Maintenance Service",
		processName:  "agent_maintenance.exe",
		installPaths: []string{
			"C:\\Program Files (x86)\\N-able Technologies",
		},
	},
	"Pulseway": {
		serviceName:  "Pulseway",
		processName:  "PCMonitorSrv.exe",
		installPaths: []string{
			"C:\\Program Files (x86)\\MMSOFT Design\\PC Monitor",
		},
	},
	"TeamViewer": {
		serviceName:  "TeamViewer",
		processName:  "TeamViewer_Service.exe",
		installPaths: []string{
			"C:\\Program Files\\TeamViewer",
			"C:\\Program Files (x86)\\TeamViewer",
		},
	},
}

// RMMCheck detects installed RMM agents for strategic intelligence.
//
// This is NOT a compliance check - it's strategic intelligence for MSP displacement.
// The information helps identify competing MSP tooling on managed devices.
type RMMCheck struct{}

// Name returns the check identifier
func (c *RMMCheck) Name() string {
	return "rmm_detection"
}

// Run executes the RMM detection check
func (c *RMMCheck) Run(ctx context.Context) CheckResult {
	result := CheckResult{
		CheckType:    "rmm_detection",
		HIPAAControl: "", // Not a compliance check
		Metadata:     make(map[string]string),
	}

	detected := []RMMAgent{}

	// Get running services
	serviceMap, err := getRunningServices(ctx)
	if err != nil {
		result.Metadata["service_query_error"] = err.Error()
	}

	// Get running processes
	processMap, err := getRunningProcesses(ctx)
	if err != nil {
		result.Metadata["process_query_error"] = err.Error()
	}

	// Check each known RMM
	for rmmName, sig := range knownRMM {
		found := false
		method := ""
		var agent RMMAgent

		// Check service
		if serviceMap != nil {
			if _, exists := serviceMap[strings.ToLower(sig.serviceName)]; exists {
				found = true
				method = "service"
			}
		}

		// Check process
		if !found && processMap != nil {
			if _, exists := processMap[strings.ToLower(sig.processName)]; exists {
				found = true
				method = "process"
			}
		}

		// Check install paths
		if !found {
			for _, path := range sig.installPaths {
				if _, err := os.Stat(path); err == nil {
					found = true
					method = "path"
					break
				}
			}
		}

		if found {
			agent = RMMAgent{
				Name:     rmmName,
				Running:  method == "service" || method == "process",
				DetectBy: method,
			}
			detected = append(detected, agent)
			result.Metadata[fmt.Sprintf("rmm_%s", sanitizeKey(rmmName))] = method
		}
	}

	result.Metadata["rmm_count"] = fmt.Sprintf("%d", len(detected))

	// Build detected list for actual field
	var detectedNames []string
	for _, agent := range detected {
		detectedNames = append(detectedNames, agent.Name)
	}

	if len(detected) > 0 {
		result.Passed = true // Not a failure - just information
		result.Expected = "RMM detection complete"
		result.Actual = fmt.Sprintf("Detected: %s", strings.Join(detectedNames, ", "))
	} else {
		result.Passed = true
		result.Expected = "RMM detection complete"
		result.Actual = "No RMM agents detected"
	}

	return result
}

// GetDetectedRMM returns list of detected RMM agents (for API reporting)
func (c *RMMCheck) GetDetectedRMM(ctx context.Context) []RMMAgent {
	result := c.Run(ctx)
	if result.Error != nil {
		return nil
	}

	// Parse from metadata
	detected := []RMMAgent{}
	for rmmName := range knownRMM {
		key := fmt.Sprintf("rmm_%s", sanitizeKey(rmmName))
		if method, ok := result.Metadata[key]; ok {
			detected = append(detected, RMMAgent{
				Name:     rmmName,
				Running:  method == "service" || method == "process",
				DetectBy: method,
			})
		}
	}

	return detected
}

// getRunningServices returns map of running service names (lowercase)
func getRunningServices(ctx context.Context) (map[string]bool, error) {
	services, err := wmi.Query(ctx,
		"root\\CIMV2",
		"SELECT Name FROM Win32_Service WHERE State = 'Running'",
	)
	if err != nil {
		return nil, err
	}

	result := make(map[string]bool)
	for _, svc := range services {
		if name, ok := wmi.GetPropertyString(svc, "Name"); ok {
			result[strings.ToLower(name)] = true
		}
	}
	return result, nil
}

// getRunningProcesses returns map of running process names (lowercase)
func getRunningProcesses(ctx context.Context) (map[string]bool, error) {
	processes, err := wmi.Query(ctx,
		"root\\CIMV2",
		"SELECT Name FROM Win32_Process",
	)
	if err != nil {
		return nil, err
	}

	result := make(map[string]bool)
	for _, proc := range processes {
		if name, ok := wmi.GetPropertyString(proc, "Name"); ok {
			result[strings.ToLower(name)] = true
		}
	}
	return result, nil
}

// sanitizeKey converts RMM name to metadata key format
func sanitizeKey(name string) string {
	key := strings.ToLower(name)
	key = strings.ReplaceAll(key, " ", "_")
	key = strings.ReplaceAll(key, "-", "_")
	return key
}
