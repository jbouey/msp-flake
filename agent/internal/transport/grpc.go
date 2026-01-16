// Package transport handles communication with the appliance.
package transport

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"io"
	"os"
	"sync"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/keepalive"

	"github.com/osiriscare/agent/internal/config"
	pb "github.com/osiriscare/agent/proto"
)

// Version is set at build time
var Version = "0.1.0"

// GRPCClient manages the gRPC connection to the appliance
type GRPCClient struct {
	conn      *grpc.ClientConn
	client    pb.ComplianceAgentClient
	agentID   string
	hostname  string
	connected bool
	mu        sync.RWMutex
	config    *config.Config

	// For streaming drift events
	driftStream pb.ComplianceAgent_ReportDriftClient

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
	c.client = pb.NewComplianceAgentClient(conn)
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
func (c *GRPCClient) Register(ctx context.Context) (*pb.RegisterResponse, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	if !c.connected {
		return nil, fmt.Errorf("not connected")
	}

	req := &pb.RegisterRequest{
		Hostname:     c.hostname,
		OsVersion:    getOSVersion(),
		AgentVersion: Version,
		MacAddress:   getMACAddress(),
	}

	resp, err := c.client.Register(ctx, req)
	if err != nil {
		return nil, fmt.Errorf("register failed: %w", err)
	}

	c.agentID = resp.AgentId
	return resp, nil
}

// StartDriftStream starts the bidirectional drift streaming
func (c *GRPCClient) StartDriftStream(ctx context.Context) error {
	c.mu.Lock()
	defer c.mu.Unlock()

	if !c.connected {
		return fmt.Errorf("not connected")
	}

	stream, err := c.client.ReportDrift(ctx)
	if err != nil {
		return fmt.Errorf("failed to start drift stream: %w", err)
	}

	c.driftStream = stream
	return nil
}

// SendDrift sends a drift event via the stream
func (c *GRPCClient) SendDrift(ctx context.Context, event *pb.DriftEvent) error {
	c.mu.Lock()
	defer c.mu.Unlock()

	if !c.connected {
		return fmt.Errorf("not connected")
	}

	// Fill in agent ID
	event.AgentId = c.agentID
	event.Hostname = c.hostname
	event.Timestamp = time.Now().Unix()

	if c.driftStream != nil {
		// Use streaming
		if err := c.driftStream.Send(event); err != nil {
			return fmt.Errorf("failed to send drift: %w", err)
		}
		return nil
	}

	// Fall back to one-shot stream
	stream, err := c.client.ReportDrift(ctx)
	if err != nil {
		return fmt.Errorf("failed to create drift stream: %w", err)
	}

	if err := stream.Send(event); err != nil {
		return fmt.Errorf("failed to send drift: %w", err)
	}

	// Receive ack
	if _, err := stream.Recv(); err != nil && err != io.EOF {
		return fmt.Errorf("failed to receive ack: %w", err)
	}

	stream.CloseSend()
	return nil
}

// SendHeartbeat sends a heartbeat to the appliance
func (c *GRPCClient) SendHeartbeat(ctx context.Context) (*pb.HeartbeatResponse, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	if !c.connected {
		return nil, fmt.Errorf("not connected")
	}

	req := &pb.HeartbeatRequest{
		AgentId:   c.agentID,
		Timestamp: time.Now().Unix(),
	}

	resp, err := c.client.Heartbeat(ctx, req)
	if err != nil {
		return nil, fmt.Errorf("heartbeat failed: %w", err)
	}

	return resp, nil
}

// SendHealingResult sends a healing result to the appliance
func (c *GRPCClient) SendHealingResult(ctx context.Context, result *pb.HealingResult) error {
	c.mu.Lock()
	defer c.mu.Unlock()

	if !c.connected {
		return fmt.Errorf("not connected")
	}

	result.AgentId = c.agentID
	result.Hostname = c.hostname
	result.Timestamp = time.Now().Unix()

	_, err := c.client.ReportHealing(ctx, result)
	if err != nil {
		return fmt.Errorf("report healing failed: %w", err)
	}

	return nil
}

// SendRMMStatus sends RMM detection status to the appliance
func (c *GRPCClient) SendRMMStatus(ctx context.Context, agents []*pb.RMMAgent) error {
	c.mu.Lock()
	defer c.mu.Unlock()

	if !c.connected {
		return fmt.Errorf("not connected")
	}

	req := &pb.RMMStatusReport{
		AgentId:        c.agentID,
		Hostname:       c.hostname,
		DetectedAgents: agents,
		Timestamp:      time.Now().Unix(),
	}

	_, err := c.client.ReportRMMStatus(ctx, req)
	if err != nil {
		return fmt.Errorf("report RMM status failed: %w", err)
	}

	return nil
}

// IsConnected returns the connection status
func (c *GRPCClient) IsConnected() bool {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.connected
}

// GetAgentID returns the registered agent ID
func (c *GRPCClient) GetAgentID() string {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.agentID
}

// Close closes the gRPC connection
func (c *GRPCClient) Close() error {
	c.mu.Lock()
	defer c.mu.Unlock()

	if c.driftStream != nil {
		c.driftStream.CloseSend()
	}

	if c.conn != nil {
		return c.conn.Close()
	}
	return nil
}

// Reconnect attempts to reconnect to the appliance
func (c *GRPCClient) Reconnect(ctx context.Context) error {
	c.mu.Lock()
	defer c.mu.Unlock()

	if c.driftStream != nil {
		c.driftStream.CloseSend()
		c.driftStream = nil
	}

	if c.conn != nil {
		c.conn.Close()
	}
	c.connected = false
	c.agentID = ""

	return c.connect(ctx)
}

// Helper functions

func getOSVersion() string {
	// On Windows, this would use syscalls to get version
	// For now, return a placeholder
	return "Windows"
}

func getMACAddress() string {
	// This would get the primary MAC address
	// For now, return empty
	return ""
}
