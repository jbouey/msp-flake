// appliance-watchdog — Session 207 Phase W1.
//
// Second systemd unit that runs alongside the main appliance daemon.
// 2-minute checkin loop to /api/watchdog/checkin, consumes watchdog_*
// fleet orders to recover a wedged main daemon without requiring SSH.
//
// Usage:
//
//	appliance-watchdog --config /etc/msp-watchdog.yaml
package main

import (
	"context"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"github.com/osiriscare/appliance/internal/watchdog"
)

var (
	flagConfig  = flag.String("config", "/etc/msp-watchdog.yaml", "Config file path")
	flagVersion = flag.Bool("version", false, "Print version and exit")
)

func main() {
	flag.Parse()

	if *flagVersion {
		fmt.Printf("appliance-watchdog %s\n", watchdog.Version)
		os.Exit(0)
	}

	// Use slog with a JSON handler so systemd journal captures structured
	// fields — matches the logging pattern the main daemon uses post-
	// Session 202 migration.
	h := slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo})
	slog.SetDefault(slog.New(h))

	w, err := watchdog.New(*flagConfig)
	if err != nil {
		slog.Error("watchdog init failed — idling until config lands",
			"config_path", *flagConfig, "err", err)
		// Don't crash — a wedged config must not keep systemd restart-
		// looping the watchdog. Sleep until signal; the systemd unit
		// will let the operator ship a new config via a fleet order or
		// manual intervention.
		ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
		defer cancel()
		<-ctx.Done()
		os.Exit(0)
	}

	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()

	if err := w.Run(ctx); err != nil {
		slog.Error("watchdog run failed", "err", err)
		os.Exit(1)
	}
}
