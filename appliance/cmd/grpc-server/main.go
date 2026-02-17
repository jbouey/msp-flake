// Standalone gRPC server for Go agent communication.
//
// This is the Phase 1 entry point — runs as a subprocess of the Python
// appliance daemon, replacing the Python gRPC server (grpc_server.py).
//
// Usage:
//
//	grpc-server --port 50051 --site-id "site-abc" [--ca-dir /var/lib/msp/ca]
package main

import (
	"flag"
	"log"
	"os"
	"os/signal"
	"syscall"

	"github.com/osiriscare/appliance/internal/ca"
	"github.com/osiriscare/appliance/internal/grpcserver"
)

var (
	flagPort    = flag.Int("port", 50051, "gRPC listen port")
	flagSiteID  = flag.String("site-id", "", "Site ID for incident tracking")
	flagCADir   = flag.String("ca-dir", "/var/lib/msp/ca", "CA certificate directory")
	flagTLSCert = flag.String("tls-cert", "", "TLS server certificate file")
	flagTLSKey  = flag.String("tls-key", "", "TLS server key file")
	flagCACert  = flag.String("ca-cert", "", "CA certificate for mTLS client verification")
)

func main() {
	flag.Parse()

	log.SetFlags(log.LstdFlags | log.Lshortfile)
	log.Println("OsirisCare gRPC Server starting...")

	// Initialize CA for agent certificate enrollment
	var agentCA *ca.AgentCA
	if *flagCADir != "" {
		agentCA = ca.New(*flagCADir)
		if err := agentCA.EnsureCA(); err != nil {
			log.Printf("[CA] Failed to initialize: %v (cert enrollment disabled)", err)
			agentCA = nil
		} else {
			log.Printf("[CA] Initialized from %s", *flagCADir)

			// Auto-configure TLS from CA if not explicitly set
			if *flagTLSCert == "" && *flagTLSKey == "" {
				// Generate server cert using CA
				ip := getLocalIP()
				certPEM, keyPEM, err := agentCA.GenerateServerCert(ip)
				if err != nil {
					log.Printf("[CA] Failed to generate server cert: %v", err)
				} else {
					// Write temp files for TLS config
					certFile := *flagCADir + "/server.crt"
					keyFile := *flagCADir + "/server.key"
					os.WriteFile(certFile, certPEM, 0o644)
					os.WriteFile(keyFile, keyPEM, 0o600)
					*flagTLSCert = certFile
					*flagTLSKey = keyFile
					caCertFile := *flagCADir + "/ca.crt"
					*flagCACert = caCertFile
					log.Printf("[CA] Auto-configured TLS for %s", ip)
				}
			}
		}
	}

	registry := grpcserver.NewAgentRegistry()

	srv := grpcserver.NewServer(grpcserver.Config{
		Port:        *flagPort,
		TLSCertFile: *flagTLSCert,
		TLSKeyFile:  *flagTLSKey,
		CACertFile:  *flagCACert,
		SiteID:      *flagSiteID,
	}, registry, agentCA)

	// Graceful shutdown
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		sig := <-sigChan
		log.Printf("Shutdown signal: %v", sig)
		srv.GracefulStop()
	}()

	// Drain heal requests (log only in standalone mode)
	go func() {
		for req := range srv.HealChan {
			log.Printf("[heal] Received heal request: %s/%s from %s",
				req.Hostname, req.CheckType, req.AgentID)
		}
	}()

	if err := srv.Serve(); err != nil {
		log.Fatalf("Server failed: %v", err)
	}
}

func getLocalIP() string {
	// Try common appliance interface
	addrs, err := os.ReadFile("/var/lib/msp/ip_address")
	if err == nil {
		return string(addrs)
	}
	return "127.0.0.1"
}

var Version = "0.1.0"
