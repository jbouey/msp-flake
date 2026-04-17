// Package sdnotify provides minimal sd_notify integration for systemd.
// No cgo dependency — writes directly to the NOTIFY_SOCKET Unix datagram.
package sdnotify

import (
	"context"
	"net"
	"os"
	"sync"
)

var (
	once       sync.Once
	socketPath string
)

// resolveSocket reads NOTIFY_SOCKET once and unsets it from the process
// environment so that child processes spawned after daemon startup don't
// inherit it. Without this scrub, systemd logs spurious rejections like:
//
//	appliance-daemon.service: Got notification message from PID N, but
//	reception only permitted for main PID M
//
// for every child that happens to send along the socket (libsystemd-linked
// binaries like `journalctl`, or anything spawned via `systemd-run`).
func resolveSocket() {
	once.Do(func() {
		socketPath = os.Getenv("NOTIFY_SOCKET")
		if socketPath != "" {
			_ = os.Unsetenv("NOTIFY_SOCKET")
		}
	})
}

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
	resolveSocket()
	if socketPath == "" {
		return nil // Not running under systemd — silently ignore
	}

	dialer := net.Dialer{}
	conn, err := dialer.DialContext(context.Background(), "unixgram", socketPath)
	if err != nil {
		return err
	}
	defer conn.Close()

	_, err = conn.Write([]byte(state))
	return err
}
