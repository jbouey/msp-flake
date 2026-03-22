package daemon

import (
	"context"
	"fmt"
	"log"
	"path/filepath"
	"strings"

	"github.com/osiriscare/appliance/internal/maputil"
	"github.com/osiriscare/appliance/internal/winrm"
)

// handleConfigureWorkstationAgent uses the daemon's own WinRM session to
// configure and start the agent on a workstation. This bypasses the autodeploy
// chain and works when the binary is already on the workstation.
//
// Parameters:
//   hostname: target workstation hostname or IP (required)
//   appliance_addr: gRPC address for agent config (optional, auto-detected)
func (d *Daemon) handleConfigureWorkstationAgent(_ context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	hostname, _ := params["hostname"].(string)
	if hostname == "" {
		return nil, fmt.Errorf("hostname is required")
	}

	applianceAddr, _ := params["appliance_addr"].(string)
	if applianceAddr == "" {
		applianceAddr = d.config.GRPCListenAddr()
	}

	// Build WinRM target using credential lookup (same as driftscan)
	target := d.buildWinRMTargetByHostname(hostname)
	if target == nil {
		return nil, fmt.Errorf("no WinRM credentials for %s", hostname)
	}

	results := map[string]interface{}{
		"hostname": hostname,
		"steps":    []string{},
	}
	addStep := func(step string) {
		results["steps"] = append(results["steps"].([]string), step)
	}

	// Step 1: Set execution policy to allow scripts
	log.Printf("[configure-agent] [%s] Step 1: Setting execution policy", hostname)
	epResult := d.winrmExec.Execute(target,
		`Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope LocalMachine -Force; Get-ExecutionPolicy`,
		"AGENT-CFG-EP", "configure", 15, 0, 10.0, nil)
	if epResult.Success {
		stdout := maputil.String(epResult.Output, "std_out")
		addStep(fmt.Sprintf("execution_policy=%s", strings.TrimSpace(stdout)))
	} else {
		addStep(fmt.Sprintf("execution_policy_failed=%s", epResult.Error))
	}

	// Step 2: Check binary exists and version matches
	log.Printf("[configure-agent] [%s] Step 2: Checking binary", hostname)
	expectedVersion := readVersionFile(filepath.Join(d.config.StateDir, "agent"))
	checkResult := d.winrmExec.Execute(target,
		fmt.Sprintf(`$exe = "C:\OsirisCare\osiris-agent.exe"
if (Test-Path $exe) {
    $fi = Get-Item $exe
    $ver = "unknown"
    try { $out = & $exe --version 2>&1; if ($out -match "(\d+\.\d+\.\d+)") { $ver = $matches[1] } } catch {}
    @{ exists=$true; size=$fi.Length; version=$ver; expected="%s" } | ConvertTo-Json -Compress
} else {
    @{ exists=$false } | ConvertTo-Json -Compress
}`, expectedVersion),
		"AGENT-CFG-CHECK", "configure", 20, 0, 10.0, nil)
	if !checkResult.Success {
		return nil, fmt.Errorf("binary check failed: %s", checkResult.Error)
	}
	checkStdout := maputil.String(checkResult.Output, "std_out")
	addStep(fmt.Sprintf("binary=%s", strings.TrimSpace(checkStdout)))

	// If binary missing or wrong version, download from appliance file server
	needsDownload := strings.Contains(checkStdout, `"exists":false`)
	if !needsDownload && expectedVersion != "" {
		needsDownload = !strings.Contains(checkStdout, fmt.Sprintf(`"version":"%s"`, expectedVersion))
	}

	if needsDownload {
		log.Printf("[configure-agent] [%s] Step 2b: Downloading correct binary", hostname)
		applianceIP := d.config.GRPCListenAddr()
		if idx := strings.LastIndex(applianceIP, ":"); idx >= 0 {
			applianceIP = applianceIP[:idx]
		}
		downloadURL := fmt.Sprintf("http://%s:8090/agent/osiris-agent.exe", applianceIP)

		// Try HTTP download first
		dlScript := fmt.Sprintf(`
New-Item -ItemType Directory -Force -Path "C:\OsirisCare" | Out-Null
Stop-Service -Name "OsirisCareAgent" -Force -EA SilentlyContinue
Start-Sleep -Seconds 1
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $wc = New-Object System.Net.WebClient
    $wc.DownloadFile("%s", "C:\OsirisCare\osiris-agent.exe")
    $fi = Get-Item "C:\OsirisCare\osiris-agent.exe"
    @{ Success=$true; Size=$fi.Length; Method="HTTP" } | ConvertTo-Json -Compress
} catch {
    @{ Success=$false; Error=$_.Exception.Message; Method="HTTP" } | ConvertTo-Json -Compress
}`, downloadURL)
		dlResult := d.winrmExec.Execute(target, dlScript, "AGENT-CFG-DL", "configure", 120, 0, 10.0, nil)
		dlStdout := ""
		if dlResult.Success {
			dlStdout = maputil.String(dlResult.Output, "std_out")
		}

		if !dlResult.Success || strings.Contains(dlStdout, `"Success":false`) {
			// HTTP failed — try NETLOGON UNC
			log.Printf("[configure-agent] [%s] HTTP download failed, trying NETLOGON", hostname)
			if d.config.DomainController != nil && *d.config.DomainController != "" {
				dcIP := *d.config.DomainController
				uncScript := fmt.Sprintf(`
Stop-Service -Name "OsirisCareAgent" -Force -EA SilentlyContinue
net use "\\%s\NETLOGON" /delete 2>$null
try {
    Copy-Item -Path "\\%s\NETLOGON\osiris-agent.exe" -Destination "C:\OsirisCare\osiris-agent.exe" -Force
    $fi = Get-Item "C:\OsirisCare\osiris-agent.exe"
    @{ Success=$true; Size=$fi.Length; Method="NETLOGON" } | ConvertTo-Json -Compress
} catch {
    @{ Success=$false; Error=$_.Exception.Message; Method="NETLOGON" } | ConvertTo-Json -Compress
}`, dcIP, dcIP)
				uncResult := d.winrmExec.Execute(target, uncScript, "AGENT-CFG-UNC", "configure", 60, 0, 10.0, nil)
				if uncResult.Success {
					dlStdout = maputil.String(uncResult.Output, "std_out")
				}
			}
		}
		addStep(fmt.Sprintf("download=%s", strings.TrimSpace(dlStdout)))
		log.Printf("[configure-agent] [%s] Download result: %s", hostname, strings.TrimSpace(dlStdout))
	}

	// Step 3: Write config to BOTH locations (install dir + data dir)
	log.Printf("[configure-agent] [%s] Step 3: Writing config (addr=%s)", hostname, applianceAddr)
	configJSON := fmt.Sprintf(`{"appliance_addr":"%s","check_interval":300,"data_dir":"C:\\ProgramData\\OsirisCare"}`, applianceAddr)
	configScript := fmt.Sprintf(
		`New-Item -ItemType Directory -Force -Path "C:\ProgramData\OsirisCare" | Out-Null; Set-Content -Path "C:\OsirisCare\config.json" -Value '%s' -Encoding UTF8 -Force; Set-Content -Path "C:\ProgramData\OsirisCare\config.json" -Value '%s' -Encoding UTF8 -Force; "OK"`,
		configJSON, configJSON)
	cfgResult := d.winrmExec.Execute(target, configScript, "AGENT-CFG-WRITE", "configure", 15, 0, 10.0, nil)
	if !cfgResult.Success {
		return nil, fmt.Errorf("write config failed: %s", cfgResult.Error)
	}
	addStep("config_written")

	// Step 3b: Clear stale TLS certificates (forces re-enrollment with current appliance)
	log.Printf("[configure-agent] [%s] Step 3b: Clearing stale TLS certs", hostname)
	certCleanScript := `
$paths = @("C:\OsirisCare\agent.crt", "C:\OsirisCare\agent.key", "C:\OsirisCare\ca.crt",
           "C:\ProgramData\OsirisCare\agent.crt", "C:\ProgramData\OsirisCare\agent.key", "C:\ProgramData\OsirisCare\ca.crt")
$removed = 0
foreach ($p in $paths) { if (Test-Path $p) { Remove-Item $p -Force; $removed++ } }
"removed=$removed"
`
	certResult := d.winrmExec.Execute(target, certCleanScript, "AGENT-CFG-CERTS", "configure", 15, 0, 10.0, nil)
	if certResult.Success {
		certStdout := maputil.String(certResult.Output, "std_out")
		addStep(fmt.Sprintf("certs=%s", strings.TrimSpace(certStdout)))
	}

	// Step 4: Open firewall for outbound gRPC (port 50051)
	log.Printf("[configure-agent] [%s] Step 4: Firewall rule for gRPC", hostname)
	fwResult := d.winrmExec.Execute(target,
		`$r = Get-NetFirewallRule -DisplayName "OsirisCare Agent gRPC" -EA SilentlyContinue; if (-not $r) { New-NetFirewallRule -DisplayName "OsirisCare Agent gRPC" -Direction Outbound -Action Allow -Protocol TCP -RemotePort 50051 -Program "C:\OsirisCare\osiris-agent.exe" | Out-Null; "created" } else { "exists" }`,
		"AGENT-CFG-FW", "configure", 15, 0, 10.0, nil)
	if fwResult.Success {
		fwStdout := maputil.String(fwResult.Output, "std_out")
		addStep(fmt.Sprintf("firewall=%s", strings.TrimSpace(fwStdout)))
	} else {
		addStep(fmt.Sprintf("firewall_failed=%s", fwResult.Error))
	}

	// Step 5: Also open inbound HTTP from appliance (for binary downloads)
	fwHTTPResult := d.winrmExec.Execute(target,
		`$r = Get-NetFirewallRule -DisplayName "OsirisCare Appliance HTTP" -EA SilentlyContinue; if (-not $r) { New-NetFirewallRule -DisplayName "OsirisCare Appliance HTTP" -Direction Outbound -Action Allow -Protocol TCP -RemotePort 8090 | Out-Null; "created" } else { "exists" }`,
		"AGENT-CFG-FW-HTTP", "configure", 15, 0, 10.0, nil)
	if fwHTTPResult.Success {
		httpStdout := maputil.String(fwHTTPResult.Output, "std_out")
		addStep(fmt.Sprintf("firewall_http=%s", strings.TrimSpace(httpStdout)))
	}

	// Step 6: Install and start service
	log.Printf("[configure-agent] [%s] Step 5: Installing service", hostname)
	svcScript := `
$serviceName = "OsirisCareAgent"
$exePath = "C:\OsirisCare\osiris-agent.exe"
$configPath = "C:\OsirisCare\config.json"

$existing = Get-Service -Name $serviceName -EA SilentlyContinue
if ($existing -and $existing.Status -eq "Running") {
    Stop-Service -Name $serviceName -Force
    Start-Sleep -Seconds 2
}
if ($existing) {
    sc.exe delete $serviceName | Out-Null
    Start-Sleep -Seconds 2
}

New-Service -Name $serviceName -BinaryPathName """$exePath"" --config ""$configPath""" -DisplayName "OsirisCare Compliance Agent" -Description "HIPAA compliance monitoring agent" -StartupType Automatic -ErrorAction Stop

Start-Service -Name $serviceName -ErrorAction Stop
sc.exe failure $serviceName reset= 86400 actions= restart/60000/restart/60000/restart/60000 | Out-Null

Start-Sleep -Seconds 3
$svc = Get-Service -Name $serviceName
@{ status = $svc.Status.ToString(); startType = $svc.StartType.ToString() } | ConvertTo-Json -Compress
`
	svcResult := d.winrmExec.Execute(target, svcScript, "AGENT-CFG-SVC", "configure", 30, 0, 10.0, nil)
	if !svcResult.Success {
		return nil, fmt.Errorf("service install failed: %s", svcResult.Error)
	}
	svcStdout := maputil.String(svcResult.Output, "std_out")
	addStep(fmt.Sprintf("service=%s", strings.TrimSpace(svcStdout)))
	log.Printf("[configure-agent] [%s] Service result: %s", hostname, strings.TrimSpace(svcStdout))

	results["success"] = true
	results["message"] = fmt.Sprintf("Agent configured on %s — should register via gRPC within 30 seconds", hostname)
	return results, nil
}

// buildWinRMTargetByHostname creates a WinRM target for a hostname using the daemon's
// credential lookup (same as driftscan uses).
func (d *Daemon) buildWinRMTargetByHostname(hostname string) *winrm.Target {
	cfg := d.config

	// Try credential lookup by hostname, then by resolved IP
	username := ""
	password := ""
	connectHost := hostname

	if wt, ok := d.LookupWinTarget(hostname); ok {
		username = wt.Username
		password = wt.Password
		connectHost = wt.Hostname
	}

	// Final fallback: DC admin credentials
	if username == "" && cfg.DCUsername != nil && cfg.DCPassword != nil {
		username = *cfg.DCUsername
		password = *cfg.DCPassword
	}

	if username == "" {
		return nil
	}

	ws := d.probeWinRM(connectHost)
	return &winrm.Target{
		Hostname:  connectHost,
		Port:      ws.Port,
		Username:  username,
		Password:  password,
		UseSSL:    ws.UseSSL,
		VerifySSL: false,
	}
}
