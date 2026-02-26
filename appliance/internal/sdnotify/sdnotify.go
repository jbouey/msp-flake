// Package sdnotify provides minimal sd_notify integration for systemd.
// No cgo dependency — writes directly to the NOTIFY_SOCKET Unix datagram.
package sdnotify

import (
	"net"
	"os"
)

// Ready sends READY=1 to systemd, signaling the service is ready.
func Ready() error {
	return notify("READY=1")
}

// Watchdog sends WATCHDOG=1 to systemd, resetting the watchdog timer.
func Watchdog() error {
	return notify("WATCHDOG=1")
}

// Stopping sends STOPPING=1 to systemd, signaling graceful shutdown.
func Stopping() error {
	return notify("STOPPING=1")
}

// Status sends STATUS=<msg> to systemd for display in systemctl status.
func Status(msg string) error {
	return notify("STATUS=" + msg)
}

func notify(state string) error {
	socketPath := os.Getenv("NOTIFY_SOCKET")
	if socketPath == "" {
		return nil // Not running under systemd — silently ignore
	}

	conn, err := net.Dial("unixgram", socketPath)
	if err != nil {
		return err
	}
	defer conn.Close()

	_, err = conn.Write([]byte(state))
	return err
}
