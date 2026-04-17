package sdnotify

import (
	"net"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

// resetForTest clears the package-level cache so each test runs fresh.
func resetForTest(t *testing.T) {
	t.Helper()
	once = sync.Once{}
	socketPath = ""
}

func TestEnvScrub_UnsetsNOTIFY_SOCKET(t *testing.T) {
	resetForTest(t)

	// macOS caps unix socket paths at 104 chars — t.TempDir() paths are too
	// long. Put it in /tmp directly.
	dir, err := os.MkdirTemp("/tmp", "sdn")
	if err != nil {
		t.Fatalf("tempdir: %v", err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	sockPath := filepath.Join(dir, "n.sock")
	t.Setenv("NOTIFY_SOCKET", sockPath)

	ln, err := net.ListenPacket("unixgram", sockPath)
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	defer ln.Close()

	if err := Ready(); err != nil {
		t.Fatalf("Ready: %v", err)
	}

	// After first call, NOTIFY_SOCKET must be scrubbed so child processes
	// spawned post-startup don't inherit it and trigger spurious
	// "reception only permitted for main PID" rejections in systemd.
	if got := os.Getenv("NOTIFY_SOCKET"); got != "" {
		t.Fatalf("NOTIFY_SOCKET leaked to environment after Ready(): %q", got)
	}
}

func TestEnvScrub_SubsequentCallsStillWork(t *testing.T) {
	resetForTest(t)

	// macOS caps unix socket paths at 104 chars — t.TempDir() paths are too
	// long. Put it in /tmp directly.
	dir, err := os.MkdirTemp("/tmp", "sdn")
	if err != nil {
		t.Fatalf("tempdir: %v", err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	sockPath := filepath.Join(dir, "n.sock")
	t.Setenv("NOTIFY_SOCKET", sockPath)

	ln, err := net.ListenPacket("unixgram", sockPath)
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	defer ln.Close()

	got := make(chan string, 4)
	go func() {
		buf := make([]byte, 1024)
		for {
			_ = ln.(*net.UnixConn).SetReadDeadline(time.Now().Add(2 * time.Second))
			n, _, err := ln.ReadFrom(buf)
			if err != nil {
				return
			}
			got <- string(buf[:n])
		}
	}()

	for _, fn := range []func() error{Ready, Watchdog, Stopping} {
		if err := fn(); err != nil {
			t.Fatalf("notify call: %v", err)
		}
	}

	deadline := time.After(3 * time.Second)
	msgs := make(map[string]bool)
	for len(msgs) < 3 {
		select {
		case m := <-got:
			msgs[m] = true
		case <-deadline:
			t.Fatalf("timed out waiting for notify messages; got %v", msgs)
		}
	}
	for _, want := range []string{"READY=1", "WATCHDOG=1", "STOPPING=1"} {
		if !msgs[want] {
			t.Errorf("missing %q in notify messages; got %v", want, msgs)
		}
	}
}

func TestNoSocket_SilentlyIgnored(t *testing.T) {
	resetForTest(t)
	t.Setenv("NOTIFY_SOCKET", "")

	if err := Ready(); err != nil {
		t.Fatalf("Ready should be no-op when NOTIFY_SOCKET empty: %v", err)
	}
}
