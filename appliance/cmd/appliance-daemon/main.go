// OsirisCare Appliance Daemon (Go).
//
// Replaces the Python appliance_agent.py as the main daemon on NixOS appliances.
// Embeds the gRPC server, handles phone-home checkin, L1 healing, and
// routes L2 requests to a Python sidecar via Unix socket.
//
// Usage:
//
//	appliance-daemon --config /var/lib/msp/config.yaml
package main

import (
	"context"
	"flag"
	"log"
	"os"
	"os/signal"
	"syscall"

	"github.com/osiriscare/appliance/internal/daemon"
)

var (
	flagConfig  = flag.String("config", "/var/lib/msp/config.yaml", "Config file path")
	flagVersion = flag.Bool("version", false, "Print version and exit")
)

func main() {
	flag.Parse()

	if *flagVersion {
		log.Printf("appliance-daemon %s", daemon.Version)
		os.Exit(0)
	}

	log.SetFlags(log.LstdFlags | log.Lshortfile)

	cfg, err := daemon.LoadConfig(*flagConfig)
	if err != nil {
		log.Fatalf("Failed to load config: %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		sig := <-sigChan
		log.Printf("Shutdown signal: %v", sig)
		cancel()
	}()

	d := daemon.New(cfg)
	if err := d.Run(ctx); err != nil {
		log.Fatalf("Daemon failed: %v", err)
	}
}
