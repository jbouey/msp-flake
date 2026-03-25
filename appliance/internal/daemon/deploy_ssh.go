// deploy_ssh.go — SSH-based agent deployment for Linux and macOS
package daemon

import (
	"context"
	"encoding/base64"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"

	"github.com/osiriscare/appliance/internal/sshexec"
)

// deployViaSSH deploys the OsirisCare agent to a Linux or macOS host over SSH.
// It uploads the binary via base64-chunked transfer and then runs the install script.
func (d *Daemon) deployViaSSH(ctx context.Context, deploy PendingDeploy, siteID string) error {
	target := &sshexec.Target{
		Hostname:       deploy.IPAddress,
		Port:           22,
		Username:       deploy.Username,
		ConnectTimeout: 30,
		CommandTimeout: 120,
	}

	if deploy.SSHKey != "" {
		target.PrivateKey = &deploy.SSHKey
	} else if deploy.Password != "" {
		password := deploy.Password
		target.Password = &password
	}

	// Get the agent binary via manifest-based lookup (with fallback to legacy path).
	// PendingDeploy doesn't carry OS version, so we pass empty and let the manifest
	// skip version-compatibility checks (best effort).
	platform := NormalizeOSType(deploy.OSType)
	arch := InferArch(platform, "")
	binaryPath, binaryMeta, err := d.getAgentBinary(platform, arch, "")
	if err != nil {
		return fmt.Errorf("get agent binary: %w", err)
	}

	// Read the binary from disk
	binaryData, err := os.ReadFile(binaryPath)
	if err != nil {
		return fmt.Errorf("read binary %s: %w", binaryPath, err)
	}

	version := "unknown"
	if binaryMeta != nil {
		version = binaryMeta.Version
	}
	log.Printf("[deploy-ssh] Uploading agent binary v%s (%d bytes) to %s (%s/%s)",
		version, len(binaryData), deploy.Hostname, platform, arch)

	// Upload via base64-chunked transfer over SSH
	if err := d.uploadBinarySSH(ctx, target, binaryData); err != nil {
		return fmt.Errorf("upload binary: %w", err)
	}

	// Build and run the install script
	apiURL := d.config.APIEndpoint
	if apiURL == "" {
		apiURL = "https://api.osiriscare.net"
	}
	script := buildInstallScript(deploy.OSType, "/tmp/osiris-agent", apiURL, siteID)

	result := d.sshExec.Execute(ctx, target, script, "deploy-ssh", "install", 120, 1, 5.0, true, nil)
	if !result.Success {
		return fmt.Errorf("install script failed on %s: %s", deploy.Hostname, result.Error)
	}

	log.Printf("[deploy-ssh] Agent deployed successfully to %s", deploy.Hostname)
	return nil
}

// uploadBinarySSH transfers a binary to /tmp/osiris-agent on the remote host
// using base64-encoded chunks to avoid SSH binary transfer limitations.
func (d *Daemon) uploadBinarySSH(ctx context.Context, target *sshexec.Target, binaryData []byte) error {
	const chunkSize = 20 * 1024 // 20KB per chunk

	encoded := base64.StdEncoding.EncodeToString(binaryData)

	// Remove any existing staging files
	cleanScript := "rm -f /tmp/osiris-agent /tmp/osiris-agent.b64"
	result := d.sshExec.Execute(ctx, target, cleanScript, "deploy-ssh", "clean", 30, 0, 0, false, nil)
	if !result.Success {
		log.Printf("[deploy-ssh] Warning: clean step failed on %s: %s", target.Hostname, result.Error)
	}

	// Upload in 20KB chunks
	for i := 0; i < len(encoded); i += chunkSize {
		end := i + chunkSize
		if end > len(encoded) {
			end = len(encoded)
		}
		chunk := encoded[i:end]

		appendScript := fmt.Sprintf("echo -n '%s' >> /tmp/osiris-agent.b64", strings.ReplaceAll(chunk, "'", "'\"'\"'"))
		result := d.sshExec.Execute(ctx, target, appendScript, "deploy-ssh", "upload-chunk", 60, 0, 0, false, nil)
		if !result.Success {
			return fmt.Errorf("chunk upload failed at offset %d: %s", i, result.Error)
		}
	}

	// Decode the base64 file into the binary
	decodeScript := "base64 -d /tmp/osiris-agent.b64 > /tmp/osiris-agent && rm /tmp/osiris-agent.b64 && chmod 755 /tmp/osiris-agent"
	result = d.sshExec.Execute(ctx, target, decodeScript, "deploy-ssh", "decode", 60, 0, 0, false, nil)
	if !result.Success {
		return fmt.Errorf("base64 decode failed: %s", result.Error)
	}

	return nil
}

// buildInstallScript returns a shell script that installs the OsirisCare agent
// from /tmp/osiris-agent on the target host. Supports linux and macos.
func buildInstallScript(osType, binaryPath, apiURL, siteID string) string {
	switch osType {
	case "linux":
		return buildLinuxInstallScript(binaryPath, apiURL, siteID)
	case "macos":
		return buildMacOSInstallScript(binaryPath, apiURL, siteID)
	default:
		return "echo DEPLOY_UNSUPPORTED_OS; exit 1"
	}
}

func buildLinuxInstallScript(binaryPath, apiURL, siteID string) string {
	configJSON := fmt.Sprintf(
		`{"api_url":%q,"site_id":%q,"check_interval":300,"data_dir":"/var/lib/osiriscare"}`,
		apiURL, siteID,
	)

	return fmt.Sprintf(`#!/bin/bash
set -euo pipefail

# Create directories
mkdir -p /opt/osiriscare
mkdir -p /var/lib/osiriscare

# Install binary
mv %s /opt/osiriscare/osiris-agent
chmod 755 /opt/osiriscare/osiris-agent

# Write config
cat > /etc/osiriscare.json <<'ENDCONFIG'
%s
ENDCONFIG

# Write systemd unit
cat > /etc/systemd/system/osiriscare-agent.service <<'ENDUNIT'
[Unit]
Description=OsirisCare Compliance Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/opt/osiriscare/osiris-agent --config /etc/osiriscare.json
Restart=on-failure
RestartSec=30
User=root
WorkingDirectory=/var/lib/osiriscare

[Install]
WantedBy=multi-user.target
ENDUNIT

# Enable and start
systemctl daemon-reload
systemctl enable osiriscare-agent
systemctl restart osiriscare-agent

echo "OsirisCare agent installed and started"
`, binaryPath, configJSON)
}

func buildMacOSInstallScript(binaryPath, apiURL, siteID string) string {
	configJSON := fmt.Sprintf(
		`{"api_url":%q,"site_id":%q,"check_interval":300,"data_dir":"/Library/Application Support/OsirisCare"}`,
		apiURL, siteID,
	)

	return fmt.Sprintf(`#!/bin/bash
set -euo pipefail

# Create directories
mkdir -p /Library/OsirisCare
mkdir -p "/Library/Application Support/OsirisCare"
mkdir -p /Library/Logs/OsirisCare

# Install binary
mv %s /Library/OsirisCare/osiris-agent
chmod 755 /Library/OsirisCare/osiris-agent

# Write config
cat > /Library/OsirisCare/config.json <<'ENDCONFIG'
%s
ENDCONFIG

# Write launchd plist
cat > /Library/LaunchDaemons/net.osiriscare.agent.plist <<'ENDPLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>net.osiriscare.agent</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Library/OsirisCare/osiris-agent</string>
        <string>--config</string>
        <string>/Library/OsirisCare/config.json</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Library/Logs/OsirisCare/agent.log</string>
    <key>StandardErrorPath</key>
    <string>/Library/Logs/OsirisCare/agent-error.log</string>
</dict>
</plist>
ENDPLIST

# Load service
launchctl unload /Library/LaunchDaemons/net.osiriscare.agent.plist 2>/dev/null || true
launchctl load /Library/LaunchDaemons/net.osiriscare.agent.plist

echo "OsirisCare agent installed and started"
`, binaryPath, configJSON)
}

// uninstallViaSSH removes the Go agent from a Linux or macOS host over SSH.
func (d *Daemon) uninstallViaSSH(ctx context.Context, target *sshexec.Target, osType string) error {
	script := buildUninstallScript(osType)
	result := d.sshExec.Execute(ctx, target, script, "remove-agent", "uninstall", 60, 0, 0, true, nil)
	if !result.Success {
		return fmt.Errorf("uninstall: %s", result.Error)
	}
	stdout, _ := result.Output["stdout"].(string)
	if !strings.Contains(stdout, "UNINSTALL_SUCCESS") {
		return fmt.Errorf("uninstall: script did not emit UNINSTALL_SUCCESS (stdout: %s)", stdout)
	}
	return nil
}

// buildUninstallScript generates platform-specific uninstall commands.
func buildUninstallScript(osType string) string {
	switch osType {
	case "linux":
		return `
set -e
sudo systemctl stop osiriscare-agent 2>/dev/null || true
sudo systemctl disable osiriscare-agent 2>/dev/null || true
sudo rm -f /etc/systemd/system/osiriscare-agent.service
sudo systemctl daemon-reload
sudo rm -f /etc/osiriscare.json
sudo rm -rf /opt/osiriscare
sudo rm -rf /var/lib/osiriscare
echo "UNINSTALL_SUCCESS"
`
	case "macos":
		return `
set -e
sudo launchctl unload /Library/LaunchDaemons/net.osiriscare.agent.plist 2>/dev/null || true
sudo rm -f /Library/LaunchDaemons/net.osiriscare.agent.plist
sudo rm -rf /Library/OsirisCare
sudo rm -rf "/Library/Application Support/OsirisCare"
sudo rm -rf /Library/Logs/OsirisCare
echo "UNINSTALL_SUCCESS"
`
	default:
		return "echo UNINSTALL_UNSUPPORTED_OS; exit 1"
	}
}

// getAgentBinary finds the agent binary for the given platform, arch, and OS version.
// It first consults the agent manifest for a compatible binary. If no manifest is
// available (nil or empty), it falls back to the legacy hardcoded path convention.
// Returns the file path, optional binary metadata, and any error.
func (d *Daemon) getAgentBinary(platform, arch, osVersion string) (string, *AgentBinary, error) {
	binDir := filepath.Join(d.config.StateDir, "bin")

	// Try manifest-based lookup first.
	if d.agentManifest != nil {
		if entry := d.agentManifest.LookupCompatible(platform, arch, osVersion); entry != nil {
			path := filepath.Join(binDir, entry.Filename)
			if _, err := os.Stat(path); err == nil {
				return path, entry, nil
			}
			// Binary listed in manifest but missing on disk — fall through to legacy.
			log.Printf("[deploy-ssh] Manifest entry %s/%s points to missing file %s, trying fallback",
				platform, arch, entry.Filename)
		}
	}

	// Legacy fallback: hardcoded binary name convention.
	path, err := d.getLocalBinaryPath(legacyOSType(platform))
	if err != nil {
		return path, nil, err
	}
	return path, nil, nil
}

// getLocalBinaryPath returns the path to the cached agent binary for the given OS type.
// Returns the path (even if the file is missing) alongside any error, so callers
// can include the expected path in diagnostics.
// Preserved for backward compatibility — new code should use getAgentBinary.
func (d *Daemon) getLocalBinaryPath(osType string) (string, error) {
	var binaryName string
	switch osType {
	case "linux":
		binaryName = "osiris-agent-linux-amd64"
	case "macos":
		binaryName = "osiris-agent-darwin-amd64"
	default:
		return "", fmt.Errorf("unsupported OS type for SSH deploy: %q", osType)
	}

	path := filepath.Join(d.config.StateDir, "bin", binaryName)
	if _, err := os.Stat(path); err != nil {
		return path, fmt.Errorf("agent binary not found at %s: %w", path, err)
	}
	return path, nil
}

// legacyOSType converts a normalized platform name back to the legacy osType
// strings used by getLocalBinaryPath.
func legacyOSType(platform string) string {
	switch platform {
	case "darwin":
		return "macos"
	default:
		return platform
	}
}

// handleRemoveAgent handles a "remove_agent" fleet order.
// It looks up credentials for the target host and runs the uninstall script via SSH.
func (d *Daemon) handleRemoveAgent(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	hostname, _ := params["hostname"].(string)
	ipAddress, _ := params["ip_address"].(string)
	osType, _ := params["os_type"].(string)

	if hostname == "" && ipAddress == "" {
		return nil, fmt.Errorf("remove_agent: hostname or ip_address is required")
	}
	if osType == "" {
		return nil, fmt.Errorf("remove_agent: os_type is required (linux or macos)")
	}

	// Resolve connection address: prefer IP, fall back to hostname
	connectAddr := ipAddress
	if connectAddr == "" {
		connectAddr = hostname
	}

	// Look up stored credentials for this host
	creds := d.findCredentialsForHost(hostname, ipAddress)
	if creds == nil {
		return nil, fmt.Errorf("remove_agent: no credentials found for host %q / %q", hostname, ipAddress)
	}

	target := &sshexec.Target{
		Hostname:       connectAddr,
		Port:           22,
		Username:       creds.Username,
		ConnectTimeout: 30,
		CommandTimeout: 60,
	}
	if creds.SSHKey != "" {
		target.PrivateKey = &creds.SSHKey
	} else if creds.Password != "" {
		pw := creds.Password
		target.Password = &pw
	}

	label := hostname
	if label == "" {
		label = ipAddress
	}
	log.Printf("[remove-agent] Uninstalling agent from %s (%s)", label, osType)

	if err := d.uninstallViaSSH(ctx, target, osType); err != nil {
		return nil, fmt.Errorf("remove_agent: %w", err)
	}

	log.Printf("[remove-agent] Agent removed successfully from %s", label)
	return map[string]interface{}{
		"status":   "removed",
		"hostname": label,
		"os_type":  osType,
	}, nil
}
