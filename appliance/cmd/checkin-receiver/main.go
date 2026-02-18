// Standalone checkin receiver for Central Command.
//
// Handles the fan-in /api/appliances/checkin endpoint as a Go HTTP server,
// replacing the FastAPI endpoint in sites.py. Runs on the VPS alongside
// the existing FastAPI backend, routed via nginx.
//
// Usage:
//
//	checkin-receiver --port 8001 --db "postgres://user:pass@localhost/central_command"
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/osiriscare/appliance/internal/checkin"
)

var (
	flagPort      = flag.Int("port", 8001, "HTTP listen port")
	flagDB        = flag.String("db", "", "PostgreSQL connection string (or DATABASE_URL env)")
	flagAuthToken = flag.String("auth-token", "", "Bearer token for auth (or AUTH_TOKEN env); if empty, no auth")
)

func main() {
	flag.Parse()
	log.SetFlags(log.LstdFlags | log.Lshortfile)

	connStr := *flagDB
	if connStr == "" {
		connStr = os.Getenv("DATABASE_URL")
	}
	if connStr == "" {
		log.Fatal("database connection string required: --db or DATABASE_URL env")
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	authToken := *flagAuthToken
	if authToken == "" {
		authToken = os.Getenv("AUTH_TOKEN")
	}

	db, err := checkin.NewDB(ctx, connStr)
	if err != nil {
		log.Fatalf("Failed to connect to database: %v", err)
	}
	defer db.Close()
	log.Println("Connected to PostgreSQL")

	handler := checkin.NewHandler(db, authToken)
	if authToken != "" {
		log.Println("Bearer token auth enabled")
	} else {
		log.Println("WARNING: No auth token configured — checkin endpoint is open")
	}
	mux := http.NewServeMux()
	checkin.RegisterRoutes(mux, handler)

	// Health check
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"status":"ok"}`))
	})

	srv := &http.Server{
		Addr:         fmt.Sprintf(":%d", *flagPort),
		Handler:      mux,
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 30 * time.Second,
		IdleTimeout:  60 * time.Second,
	}

	// Graceful shutdown
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		sig := <-sigChan
		log.Printf("Shutdown signal: %v", sig)
		shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer shutdownCancel()
		srv.Shutdown(shutdownCtx)
	}()

	log.Printf("Checkin receiver listening on :%d", *flagPort)
	if err := srv.ListenAndServe(); err != http.ErrServerClosed {
		log.Fatalf("Server failed: %v", err)
	}
	log.Println("Server stopped")
}
