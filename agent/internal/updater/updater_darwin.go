//go:build darwin

package updater

import (
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
)

// restartService writes a restart script and spawns it detached.
// Uses launchctl to unload/load the agent's launchd plist.
func restartService(serviceName, dataDir string) error {
	plistLabel := "com.osiriscare.agent"
	plistPath := filepath.Join("/Library/LaunchDaemons", plistLabel+".plist")

	scriptPath := filepath.Join(dataDir, "restart-service.sh")
	script := fmt.Sprintf(`#!/bin/bash
sleep 2
launchctl unload "%s" 2>/dev/null
sleep 1
launchctl load "%s"
rm -f "$0"
`, plistPath, plistPath)

	if err := os.WriteFile(scriptPath, []byte(script), 0755); err != nil {
		return fmt.Errorf("write restart script: %w", err)
	}

	cmd := exec.Command("/bin/bash", scriptPath)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}

	if err := cmd.Start(); err != nil {
		return fmt.Errorf("spawn restart script: %w", err)
	}

	log.Printf("[updater] Restart script spawned (PID %d), service will restart momentarily", cmd.Process.Pid)
	return nil
}
