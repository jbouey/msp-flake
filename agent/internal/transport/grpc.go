// Package transport handles communication with the appliance.
package transport

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"runtime"
	"strings"
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
var Version = "0.3.0"

// HealChanSize is the buffer size for the heal command channel
const HealChanSize = 32

// GRPCClient manages the gRPC connection to the appliance
type GRPCClient struct {
	conn       *grpc.ClientConn
	client     pb.ComplianceAgentClient
	agentID    string
	hostname   string
	connected  bool
	needsCerts bool // true when no TLS certs present (enrollment needed)
	mu         sync.RWMutex
	config     *config.Config

	// For streaming drift events
	driftStream pb.ComplianceAgent_ReportDriftClient
	streamCtx   context.Context
	streamDone  chan struct{} // closed when ack reader exits

	// HealCmds delivers HealCommands from both drift acks and heartbeat responses
	HealCmds chan *pb.HealCommand

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
		HealCmds:     make(chan *pb.HealCommand, HealChanSize),
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
		// No certs yet — connect insecure for certificate enrollment
		log.Println("[gRPC] No TLS certs found, connecting insecure for enrollment")
		opts = append(opts, grpc.WithTransportCredentials(insecure.NewCredentials()))
		c.needsCerts = true
	} else {
		opts = append(opts, grpc.WithTransportCredentials(credentials.NewTLS(tlsConfig)))
		c.needsCerts = false
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

// Register registers the agent with the appliance.
// If needsCerts is true, requests certificate enrollment and reconnects with mTLS.
func (c *GRPCClient) Register(ctx context.Context) (*pb.RegisterResponse, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	if !c.connected {
		return nil, fmt.Errorf("not connected")
	}

	req := &pb.RegisterRequest{
		Hostname:          c.hostname,
		OsVersion:         getOSVersion(),
		AgentVersion:      Version,
		MacAddress:        getMACAddress(),
		NeedsCertificates: c.needsCerts,
	}

	resp, err := c.client.Register(ctx, req)
	if err != nil {
		return nil, fmt.Errorf("register failed: %w", err)
	}

	c.agentID = resp.AgentId

	// Handle certificate enrollment
	if c.needsCerts && len(resp.CaCertPem) > 0 && len(resp.AgentCertPem) > 0 && len(resp.AgentKeyPem) > 0 {
		if err := c.saveCerts(resp.CaCertPem, resp.AgentCertPem, resp.AgentKeyPem); err != nil {
			return nil, fmt.Errorf("failed to save certificates: %w", err)
		}
		log.Println("[gRPC] Certificates enrolled, reconnecting with mTLS...")

		// Close insecure connection and reconnect with TLS
		if c.conn != nil {
			c.conn.Close()
		}
		c.connected = false

		if err := c.connect(ctx); err != nil {
			return nil, fmt.Errorf("mTLS reconnect failed: %w", err)
		}
		log.Println("[gRPC] Reconnected with mTLS")
	}

	return resp, nil
}

// saveCerts writes CA, agent cert, and agent key to disk.
func (c *GRPCClient) saveCerts(caCert, agentCert, agentKey []byte) error {
	if err := os.WriteFile(c.config.CAFile, caCert, 0644); err != nil {
		return fmt.Errorf("write CA cert: %w", err)
	}
	if err := os.WriteFile(c.config.CertFile, agentCert, 0644); err != nil {
		return fmt.Errorf("write agent cert: %w", err)
	}
	if err := os.WriteFile(c.config.KeyFile, agentKey, 0600); err != nil {
		return fmt.Errorf("write agent key: %w", err)
	}
	log.Printf("[gRPC] Saved certificates to %s", c.config.DataDir)
	return nil
}

// StartDriftStream starts the bidirectional drift stream and launches
// a goroutine to read DriftAck messages (which may contain HealCommands).
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
	c.streamCtx = ctx
	c.streamDone = make(chan struct{})

	// Launch ack reader goroutine
	go c.readDriftAcks()

	return nil
}

// readDriftAcks continuously reads DriftAck messages from the server.
// When a DriftAck contains a HealCommand, it's sent to the HealCmds channel.
func (c *GRPCClient) readDriftAcks() {
	defer close(c.streamDone)

	for {
		c.mu.RLock()
		stream := c.driftStream
		c.mu.RUnlock()

		if stream == nil {
			return
		}

		ack, err := stream.Recv()
		if err != nil {
			if err == io.EOF {
				log.Println("[gRPC] Drift stream closed by server")
			} else {
				log.Printf("[gRPC] Drift stream recv error: %v", err)
			}
			return
		}

		if ack.Error != "" {
			log.Printf("[gRPC] Server reported error for event %s: %s", ack.EventId, ack.Error)
		}

		// Check for embedded heal command
		if cmd := ack.GetHealCommand(); cmd != nil {
			log.Printf("[gRPC] Received heal command via drift ack: %s/%s (id=%s)",
				cmd.CheckType, cmd.Action, cmd.CommandId)
			select {
			case c.HealCmds <- cmd:
			default:
				log.Printf("[gRPC] Heal command channel full, dropping command %s", cmd.CommandId)
			}
		}
	}
}

// StreamActive returns true if the drift stream ack reader is still running.
func (c *GRPCClient) StreamActive() bool {
	c.mu.RLock()
	done := c.streamDone
	c.mu.RUnlock()

	if done == nil {
		return false
	}
	select {
	case <-done:
		return false
	default:
		return true
	}
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
		// Use persistent stream — acks are read by the ack goroutine
		if err := c.driftStream.Send(event); err != nil {
			return fmt.Errorf("failed to send drift: %w", err)
		}
		return nil
	}

	// Fall back to one-shot stream (no persistent stream active)
	stream, err := c.client.ReportDrift(ctx)
	if err != nil {
		return fmt.Errorf("failed to create drift stream: %w", err)
	}

	if err := stream.Send(event); err != nil {
		return fmt.Errorf("failed to send drift: %w", err)
	}

	// Receive ack and check for heal command
	ack, err := stream.Recv()
	if err != nil && err != io.EOF {
		return fmt.Errorf("failed to receive ack: %w", err)
	}
	if ack != nil {
		if cmd := ack.GetHealCommand(); cmd != nil {
			log.Printf("[gRPC] Received heal command via one-shot ack: %s/%s (id=%s)",
				cmd.CheckType, cmd.Action, cmd.CommandId)
			select {
			case c.HealCmds <- cmd:
			default:
				log.Printf("[gRPC] Heal command channel full, dropping command %s", cmd.CommandId)
			}
		}
	}

	stream.CloseSend()
	return nil
}

// SendHeartbeat sends a heartbeat to the appliance and delivers any
// pending HealCommands from the response to the HealCmds channel.
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

	// Deliver pending commands from heartbeat response
	for _, cmd := range resp.GetPendingCommands() {
		log.Printf("[gRPC] Received heal command via heartbeat: %s/%s (id=%s)",
			cmd.CheckType, cmd.Action, cmd.CommandId)
		select {
		case c.HealCmds <- cmd:
		default:
			log.Printf("[gRPC] Heal command channel full, dropping command %s", cmd.CommandId)
		}
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
		c.driftStream = nil
	}

	// Wait for ack reader to exit
	if c.streamDone != nil {
		select {
		case <-c.streamDone:
		case <-time.After(2 * time.Second):
		}
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

	// Wait for ack reader to exit
	if c.streamDone != nil {
		select {
		case <-c.streamDone:
		case <-time.After(2 * time.Second):
		}
		c.streamDone = nil
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
	if runtime.GOOS != "windows" {
		return runtime.GOOS + "/" + runtime.GOARCH
	}
	// On Windows, read version from registry or RtlGetVersion
	// For cross-compiled builds, return what we know
	return "Windows/" + runtime.GOARCH
}

func getMACAddress() string {
	ifaces, err := net.Interfaces()
	if err != nil {
		return ""
	}
	for _, iface := range ifaces {
		// Skip loopback, down, and virtual interfaces
		if iface.Flags&net.FlagLoopback != 0 || iface.Flags&net.FlagUp == 0 {
			continue
		}
		mac := iface.HardwareAddr.String()
		if mac == "" {
			continue
		}
		// Skip common virtual adapter prefixes
		if strings.HasPrefix(mac, "00:00:00") {
			continue
		}
		return mac
	}
	return ""
}
