// Package transport handles communication with the appliance.
package transport

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"os"
	"sync"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/keepalive"

	"github.com/osiriscare/agent/internal/config"
)

// Version is set at build time
var Version = "0.1.0"

// RegistrationResponse holds the response from agent registration
type RegistrationResponse struct {
	AgentID              string
	CheckIntervalSeconds int32
	EnabledChecks        []string
	CapabilityTier       int32
	CheckConfig          map[string]string
}

// DriftEvent represents a compliance drift to report
type DriftEvent struct {
	AgentID      string
	Hostname     string
	CheckType    string
	Passed       bool
	Expected     string
	Actual       string
	HIPAAControl string
	Timestamp    int64
	Metadata     map[string]string
}

// GRPCClient manages the gRPC connection to the appliance
type GRPCClient struct {
	conn      *grpc.ClientConn
	agentID   string
	hostname  string
	connected bool
	mu        sync.RWMutex
	config    *config.Config

	// For fallback HTTP mode
	httpEndpoint string
}

// NewGRPCClient creates a new gRPC client
func NewGRPCClient(ctx context.Context, cfg *config.Config) (*GRPCClient, error) {
	hostname, _ := os.Hostname()

	client := &GRPCClient{
		hostname:     hostname,
		config:       cfg,
		httpEndpoint: cfg.HTTPEndpoint,
	}

	if err := client.connect(ctx); err != nil {
		return nil, err
	}

	return client, nil
}

// connect establishes the gRPC connection
func (c *GRPCClient) connect(ctx context.Context) error {
	// Load TLS credentials
	tlsConfig, err := c.loadTLS()

	// Connection options
	var opts []grpc.DialOption

	if err != nil || tlsConfig == nil {
		// Fall back to insecure connection (development mode)
		opts = append(opts, grpc.WithTransportCredentials(insecure.NewCredentials()))
	} else {
		opts = append(opts, grpc.WithTransportCredentials(credentials.NewTLS(tlsConfig)))
	}

	// Add keepalive parameters
	opts = append(opts, grpc.WithKeepaliveParams(keepalive.ClientParameters{
		Time:                30 * time.Second,
		Timeout:             10 * time.Second,
		PermitWithoutStream: true,
	}))

	// Connect with timeout
	dialCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()

	conn, err := grpc.DialContext(dialCtx, c.config.ApplianceAddr, opts...)
	if err != nil {
		return fmt.Errorf("failed to dial: %w", err)
	}

	c.conn = conn
	c.connected = true

	return nil
}

// loadTLS loads TLS credentials from config
func (c *GRPCClient) loadTLS() (*tls.Config, error) {
	// Check if cert files exist
	if _, err := os.Stat(c.config.CertFile); os.IsNotExist(err) {
		return nil, fmt.Errorf("client cert not found: %s", c.config.CertFile)
	}
	if _, err := os.Stat(c.config.KeyFile); os.IsNotExist(err) {
		return nil, fmt.Errorf("client key not found: %s", c.config.KeyFile)
	}

	// Load client certificate
	cert, err := tls.LoadX509KeyPair(c.config.CertFile, c.config.KeyFile)
	if err != nil {
		return nil, fmt.Errorf("failed to load client cert: %w", err)
	}

	// Load CA certificate if provided
	var caCertPool *x509.CertPool
	if c.config.CAFile != "" {
		if _, err := os.Stat(c.config.CAFile); err == nil {
			caCert, err := os.ReadFile(c.config.CAFile)
			if err != nil {
				return nil, fmt.Errorf("failed to read CA cert: %w", err)
			}
			caCertPool = x509.NewCertPool()
			if !caCertPool.AppendCertsFromPEM(caCert) {
				return nil, fmt.Errorf("failed to parse CA cert")
			}
		}
	}

	return &tls.Config{
		Certificates: []tls.Certificate{cert},
		RootCAs:      caCertPool,
	}, nil
}

// Register registers the agent with the appliance
func (c *GRPCClient) Register(ctx context.Context) (*RegistrationResponse, error) {
	// For now, return mock registration
	// In full implementation, this would call the gRPC Register RPC

	c.agentID = fmt.Sprintf("%s-%d", c.hostname, time.Now().Unix())

	return &RegistrationResponse{
		AgentID:              c.agentID,
		CheckIntervalSeconds: 300, // 5 minutes
		EnabledChecks: []string{
			"bitlocker",
			"defender",
			"patches",
			"firewall",
			"screenlock",
			"rmm_detection",
		},
		CapabilityTier: 0, // MONITOR_ONLY
		CheckConfig:    make(map[string]string),
	}, nil
}

// SendDrift sends a drift event to the appliance
func (c *GRPCClient) SendDrift(ctx context.Context, event *DriftEvent) error {
	c.mu.Lock()
	defer c.mu.Unlock()

	if !c.connected {
		return fmt.Errorf("not connected")
	}

	// For now, just log the event
	// In full implementation, this would send via gRPC stream

	return nil
}

// SendHeartbeat sends a heartbeat to the appliance
func (c *GRPCClient) SendHeartbeat(ctx context.Context) error {
	c.mu.Lock()
	defer c.mu.Unlock()

	if !c.connected {
		return fmt.Errorf("not connected")
	}

	// In full implementation, this would send via gRPC

	return nil
}

// IsConnected returns the connection status
func (c *GRPCClient) IsConnected() bool {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.connected
}

// Close closes the gRPC connection
func (c *GRPCClient) Close() error {
	if c.conn != nil {
		return c.conn.Close()
	}
	return nil
}

// Reconnect attempts to reconnect to the appliance
func (c *GRPCClient) Reconnect(ctx context.Context) error {
	c.mu.Lock()
	defer c.mu.Unlock()

	if c.conn != nil {
		c.conn.Close()
	}
	c.connected = false

	return c.connect(ctx)
}
