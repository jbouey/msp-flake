//go:build windows

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
// The script waits for the current process to exit, then restarts the service.
func restartService(serviceName, dataDir string) error {
	scriptPath := filepath.Join(dataDir, "restart-service.cmd")

	script := fmt.Sprintf(`@echo off
timeout /t 2 /nobreak >nul
sc stop %s
:wait
sc query %s | find "STOPPED" >nul
if errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait)
timeout /t 1 /nobreak >nul
sc start %s
del "%%~f0"
`, serviceName, serviceName, serviceName)

	if err := os.WriteFile(scriptPath, []byte(script), 0755); err != nil {
		return fmt.Errorf("write restart script: %w", err)
	}

	cmd := exec.Command("cmd.exe", "/C", scriptPath)
	cmd.SysProcAttr = &syscall.SysProcAttr{
		CreationFlags: syscall.CREATE_NEW_PROCESS_GROUP | 0x00000008, // DETACHED_PROCESS
	}

	if err := cmd.Start(); err != nil {
		return fmt.Errorf("spawn restart script: %w", err)
	}

	log.Printf("[updater] Restart script spawned (PID %d), service will restart momentarily", cmd.Process.Pid)
	return nil
}
