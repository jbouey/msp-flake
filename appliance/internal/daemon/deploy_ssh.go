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

	// Get the local binary path
	binaryPath, err := d.getLocalBinaryPath(deploy.OSType)
	if err != nil {
		return fmt.Errorf("get local binary: %w", err)
	}

	// Read the binary from disk
	binaryData, err := os.ReadFile(binaryPath)
	if err != nil {
		return fmt.Errorf("read binary %s: %w", binaryPath, err)
	}

	log.Printf("[deploy-ssh] Uploading agent binary (%d bytes) to %s (%s)", len(binaryData), deploy.Hostname, deploy.OSType)

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

// getLocalBinaryPath returns the path to the cached agent binary for the given OS type.
// Returns the path (even if the file is missing) alongside any error, so callers
// can include the expected path in diagnostics.
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
