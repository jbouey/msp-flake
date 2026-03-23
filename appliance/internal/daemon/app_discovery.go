package daemon

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"strings"

	"github.com/osiriscare/appliance/internal/maputil"
	"github.com/osiriscare/appliance/internal/winrm"
)

// RunAppDiscovery runs application discovery on all Windows targets.
// Called from run_drift order with mode=app_discovery.
// Discovers services, ports, registry keys, scheduled tasks, and config files
// matching the provided hints. Results are stored in daemon.discoveryResults
// and sent to Central Command on the next checkin.
func (ds *driftScanner) RunAppDiscovery(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	log.Printf("[app_discovery] Starting app discovery scan")

	profileID := maputil.String(params, "profile_id")
	profileName := maputil.String(params, "profile_name")
	hintsRaw := maputil.Map(params, "hints")

	if profileID == "" {
		return nil, fmt.Errorf("profile_id required for app_discovery")
	}

	// Build PowerShell discovery script from hints
	script := buildDiscoveryScript(hintsRaw)

	cfg := ds.svc.Config
	if cfg.DomainController == nil || *cfg.DomainController == "" {
		return nil, fmt.Errorf("no domain controller configured")
	}
	if cfg.DCUsername == nil || cfg.DCPassword == nil {
		return nil, fmt.Errorf("no DC credentials configured")
	}

	// Run discovery on all Windows targets
	allAssets := make(map[string][]map[string]interface{})
	targets := ds.buildWindowsTargets()

	for _, t := range targets {
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		default:
		}

		log.Printf("[app_discovery] Scanning %s (%s)", t.hostname, t.label)
		result := ds.svc.WinRM.ExecuteCtx(ctx, t.target, script, "APP-DISCOVERY", "detect", 120, 1, 0, nil)
		if !result.Success {
			log.Printf("[app_discovery] Failed to scan %s: %s", t.hostname, result.Error)
			continue
		}

		// Extract stdout from the execution result
		stdout := maputil.String(result.Output, "std_out")
		if stdout == "" {
			log.Printf("[app_discovery] Empty output from %s", t.hostname)
			continue
		}

		var hostAssets map[string][]map[string]interface{}
		if err := json.Unmarshal([]byte(stdout), &hostAssets); err != nil {
			log.Printf("[app_discovery] Failed to parse results from %s: %v", t.hostname, err)
			continue
		}

		// Merge host assets into combined results
		for assetType, items := range hostAssets {
			for _, item := range items {
				item["discovered_on"] = t.hostname
			}
			allAssets[assetType] = append(allAssets[assetType], items...)
		}
		log.Printf("[app_discovery] Found assets on %s: %v", t.hostname, summarizeAssets(hostAssets))
	}

	// Store results for next checkin
	results := map[string]interface{}{
		"profile_id":   profileID,
		"profile_name": profileName,
		"assets":       allAssets,
	}

	for k, v := range results {
		ds.daemon.state.AddDiscoveryResult(k, v)
	}

	totalAssets := 0
	for _, items := range allAssets {
		totalAssets += len(items)
	}

	log.Printf("[app_discovery] Discovery complete: %d assets found across %d targets", totalAssets, len(targets))
	return map[string]interface{}{
		"status":       "discovery_complete",
		"profile_id":   profileID,
		"total_assets": totalAssets,
	}, nil
}

// buildWindowsTargets returns the list of Windows scan targets (DC + workstations).
func (ds *driftScanner) buildWindowsTargets() []scanTarget {
	cfg := ds.svc.Config
	dcWS := ds.svc.Targets.ProbeWinRMPort(*cfg.DomainController)
	targets := []scanTarget{
		{
			hostname: *cfg.DomainController,
			label:    "DC",
			target: &winrm.Target{
				Hostname:  *cfg.DomainController,
				Port:      dcWS.Port,
				Username:  *cfg.DCUsername,
				Password:  *cfg.DCPassword,
				UseSSL:    dcWS.UseSSL,
				VerifySSL: false,
			},
		},
	}

	if ds.daemon.deployer != nil {
		ds.daemon.deployer.mu.Lock()
		for hostname := range ds.daemon.deployer.deployed {
			ws := ds.svc.Targets.ProbeWinRMPort(hostname)
			targets = append(targets, scanTarget{
				hostname: hostname,
				label:    "WS",
				target: &winrm.Target{
					Hostname:  hostname,
					Port:      ws.Port,
					Username:  *cfg.DCUsername,
					Password:  *cfg.DCPassword,
					UseSSL:    ws.UseSSL,
					VerifySSL: false,
				},
			})
		}
		ds.daemon.deployer.mu.Unlock()
	}

	return targets
}

// buildDiscoveryScript generates a PowerShell script that discovers
// services, ports, registry keys, scheduled tasks matching the given hints.
func buildDiscoveryScript(hints map[string]interface{}) string {
	// Extract hint arrays
	servicePatterns := extractStringSlice(hints, "service_patterns")
	portHints := extractIntSlice(hints, "port_hints")
	registryPaths := extractStringSlice(hints, "registry_paths")
	processPatterns := extractStringSlice(hints, "process_patterns")

	var sb strings.Builder
	sb.WriteString("$result = @{}\n")

	// Discover services matching patterns
	sb.WriteString("$services = @()\n")
	if len(servicePatterns) > 0 {
		for _, p := range servicePatterns {
			fmt.Fprintf(&sb,
				"$services += Get-Service | Where-Object { $_.Name -like '%s' -or $_.DisplayName -like '%s' } | ForEach-Object {\n"+
					"  @{ name = $_.Name; display_name = $_.DisplayName; value = @{ state = $_.Status.ToString(); start_type = $_.StartType.ToString() } }\n"+
					"}\n", p, p)
		}
	} else {
		// No hints — discover all non-default services
		sb.WriteString("$services += Get-Service | Where-Object { $_.StartType -eq 'Automatic' -and $_.Status -eq 'Running' -and $_.Name -notmatch '^(wuauserv|WinDefend|Spooler|W32Time|TermService|WinRM|LanmanServer|LanmanWorkstation|Dhcp|Dnscache|EventLog|PlugPlay|Power|ProfSvc|Schedule|SENS|Themes|UserManager|Winmgmt)$' } | Select-Object -First 20 | ForEach-Object {\n" +
			"  @{ name = $_.Name; display_name = $_.DisplayName; value = @{ state = $_.Status.ToString(); start_type = $_.StartType.ToString() } }\n" +
			"}\n")
	}
	sb.WriteString("$result['service'] = $services\n\n")

	// Discover listening ports
	sb.WriteString("$ports = @()\n")
	if len(portHints) > 0 {
		portStrs := make([]string, len(portHints))
		for i, p := range portHints {
			portStrs[i] = fmt.Sprintf("%d", p)
		}
		fmt.Fprintf(&sb,
			"$targetPorts = @(%s)\n"+
				"$ports += Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $targetPorts -contains $_.LocalPort } | ForEach-Object {\n"+
				"  $proc = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue\n"+
				"  @{ name = \"TCP/$($_.LocalPort)\"; display_name = \"$($proc.Name):$($_.LocalPort)\"; value = @{ port = $_.LocalPort; protocol = 'TCP'; process = $proc.Name } }\n"+
				"} | Sort-Object { $_.name } -Unique\n",
			strings.Join(portStrs, ","))
	} else {
		sb.WriteString("$ports += Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -lt 10000 -and $_.LocalPort -notin @(135,139,445,3389,5985,5986,49152..65535) } | Select-Object -First 15 | ForEach-Object {\n" +
			"  $proc = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue\n" +
			"  @{ name = \"TCP/$($_.LocalPort)\"; display_name = \"$($proc.Name):$($_.LocalPort)\"; value = @{ port = $_.LocalPort; protocol = 'TCP'; process = $proc.Name } }\n" +
			"}\n")
	}
	sb.WriteString("$result['port'] = $ports\n\n")

	// Discover registry keys
	sb.WriteString("$regkeys = @()\n")
	if len(registryPaths) > 0 {
		for _, path := range registryPaths {
			// Handle wildcard paths
			if strings.HasSuffix(path, "\\*") {
				parent := strings.TrimSuffix(path, "\\*")
				fmt.Fprintf(&sb,
					"if (Test-Path '%s') { Get-ItemProperty -Path '%s' -ErrorAction SilentlyContinue | ForEach-Object {\n"+
						"  $_.PSObject.Properties | Where-Object { $_.Name -notmatch '^PS' } | ForEach-Object {\n"+
						"    $regkeys += @{ name = '%s\\' + $_.Name; display_name = $_.Name; value = @{ path = '%s'; value = $_.Value.ToString(); type = 'REG_SZ' } }\n"+
						"  }\n"+
						"} }\n", parent, parent, parent, parent)
			} else {
				fmt.Fprintf(&sb,
					"if (Test-Path '%s') { Get-ItemProperty -Path '%s' -ErrorAction SilentlyContinue | ForEach-Object {\n"+
						"  $_.PSObject.Properties | Where-Object { $_.Name -notmatch '^PS' } | ForEach-Object {\n"+
						"    $regkeys += @{ name = '%s\\' + $_.Name; display_name = $_.Name; value = @{ path = '%s'; value = $_.Value.ToString(); type = 'REG_SZ' } }\n"+
						"  }\n"+
						"} }\n", path, path, path, path)
			}
		}
	}
	sb.WriteString("$result['registry_key'] = $regkeys\n\n")

	// Discover scheduled tasks (non-Microsoft)
	sb.WriteString("$tasks = @()\n")
	sb.WriteString("$tasks += Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskPath -notlike '\\Microsoft\\*' -and $_.State -ne 'Disabled' } | Select-Object -First 10 | ForEach-Object {\n" +
		"  @{ name = $_.TaskName; display_name = $_.TaskPath + $_.TaskName; value = @{ state = $_.State.ToString(); task_path = $_.TaskPath } }\n" +
		"}\n")
	sb.WriteString("$result['scheduled_task'] = $tasks\n\n")

	// Discover processes matching patterns
	sb.WriteString("$procs = @()\n")
	if len(processPatterns) > 0 {
		for _, p := range processPatterns {
			fmt.Fprintf(&sb,
				"$procs += Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -like '%s' } | ForEach-Object {\n"+
					"  @{ name = $_.Name; display_name = $_.Name + ' (PID ' + $_.Id.ToString() + ')'; value = @{ working_set_mb = [math]::Round($_.WorkingSet64/1MB,1); cpu_seconds = [math]::Round($_.CPU,1) } }\n"+
					"}\n", p)
		}
	}
	sb.WriteString("$result['process'] = $procs\n\n")

	// Output as JSON
	sb.WriteString("$result | ConvertTo-Json -Depth 4 -Compress\n")

	return sb.String()
}

// extractStringSlice gets a string slice from a hints map.
func extractStringSlice(hints map[string]interface{}, key string) []string {
	raw, ok := hints[key]
	if !ok {
		return nil
	}
	switch v := raw.(type) {
	case []interface{}:
		out := make([]string, 0, len(v))
		for _, item := range v {
			if s, ok := item.(string); ok {
				out = append(out, s)
			}
		}
		return out
	case []string:
		return v
	default:
		return nil
	}
}

// extractIntSlice gets an int slice from a hints map.
func extractIntSlice(hints map[string]interface{}, key string) []int {
	raw, ok := hints[key]
	if !ok {
		return nil
	}
	switch v := raw.(type) {
	case []interface{}:
		out := make([]int, 0, len(v))
		for _, item := range v {
			switch n := item.(type) {
			case float64:
				out = append(out, int(n))
			case int:
				out = append(out, n)
			}
		}
		return out
	default:
		return nil
	}
}

// summarizeAssets produces a brief summary string like "service=3, port=2"
func summarizeAssets(assets map[string][]map[string]interface{}) string {
	parts := make([]string, 0, len(assets))
	for k, v := range assets {
		parts = append(parts, fmt.Sprintf("%s=%d", k, len(v)))
	}
	return strings.Join(parts, ", ")
}
