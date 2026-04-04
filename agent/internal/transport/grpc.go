// Package transport handles communication with the appliance.
package transport

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/keepalive"

	"github.com/osiriscare/agent/internal/config"
	pb "github.com/osiriscare/agent/proto"
)

// HealChanSize is the buffer size for the heal command channel
const HealChanSize = 128

// pinMismatchAutoRecoverThreshold is the number of consecutive TLS fingerprint
// mismatches before auto-clearing the stale pin for TOFU re-enrollment.
// Appliance self-signed certs regenerate on daemon restart, so this is expected.
const pinMismatchAutoRecoverThreshold = 3

// ErrPinMismatch is returned when the appliance TLS certificate fingerprint
// does not match the previously pinned value. Callers can check for this
// sentinel to trigger auto-recovery in the reconnect loop.
var ErrPinMismatch = fmt.Errorf("TOFU certificate pin mismatch")

// ErrCertExpired is returned when the agent's client certificate has expired
// or been revoked, requiring re-enrollment.
var ErrCertExpired = fmt.Errorf("client certificate expired or rejected")

// ClassifyConnectionError categorizes a connection error for structured logging
// and targeted recovery. Returns a short label for the error class.
func ClassifyConnectionError(err error) string {
	if err == nil {
		return "ok"
	}
	msg := err.Error()

	if errors.Is(err, ErrPinMismatch) {
		return "tls_pin_mismatch"
	}
	if errors.Is(err, ErrCertExpired) {
		return "cert_expired"
	}

	// DNS failures
	var dnsErr *net.DNSError
	if errors.As(err, &dnsErr) {
		if dnsErr.IsNotFound {
			return "dns_not_found"
		}
		return "dns_error"
	}

	// Connection refused — appliance is down but network is up
	var opErr *net.OpError
	if errors.As(err, &opErr) {
		if opErr.Op == "dial" {
			if strings.Contains(msg, "connection refused") {
				return "appliance_down"
			}
			if strings.Contains(msg, "no route to host") || strings.Contains(msg, "network is unreachable") {
				return "network_down"
			}
		}
	}

	// Timeouts
	if os.IsTimeout(err) || strings.Contains(msg, "deadline exceeded") || strings.Contains(msg, "context deadline") {
		return "timeout"
	}

	// TLS errors
	if strings.Contains(msg, "tls:") || strings.Contains(msg, "certificate") || strings.Contains(msg, "x509:") {
		return "tls_error"
	}

	// gRPC status errors
	if strings.Contains(msg, "Unavailable") {
		return "appliance_unavailable"
	}
	if strings.Contains(msg, "PermissionDenied") || strings.Contains(msg, "Unauthenticated") {
		return "auth_rejected"
	}

	return "unknown"
}

// GRPCClient manages the gRPC connection to the appliance
type GRPCClient struct {
	conn       *grpc.ClientConn
	client     pb.ComplianceAgentClient
	agentID    string
	hostname   string
	version    string // agent version, passed from main
	connected  bool
	needsCerts bool // true when no TLS certs present (enrollment needed)
	mu         sync.RWMutex
	config     *config.Config

	// pinMismatchCount tracks consecutive TLS fingerprint mismatches.
	// Accessed atomically from the TLS VerifyConnection callback.
	pinMismatchCount atomic.Int32

	// For streaming drift events
	driftStream pb.ComplianceAgent_ReportDriftClient
	streamCtx   context.Context
	streamDone  chan struct{} // closed when ack reader exits

	// HealCmds delivers HealCommands from both drift acks and heartbeat responses
	HealCmds chan *pb.HealCommand

	// For fallback HTTP mode
	httpEndpoint string

	// consecutiveFailures tracks connection failures for structured diagnostics.
	consecutiveFailures atomic.Int32
}

// ConsecutiveFailures returns the current consecutive failure count.
func (c *GRPCClient) ConsecutiveFailures() int {
	return int(c.consecutiveFailures.Load())
}

// RecordFailure increments the failure counter.
func (c *GRPCClient) RecordFailure() {
	c.consecutiveFailures.Add(1)
}

// RecordSuccess resets the failure counter.
func (c *GRPCClient) RecordSuccess() {
	c.consecutiveFailures.Store(0)
}

// NeedsCertReEnrollment returns true if the agent should attempt cert re-enrollment.
// Triggered after repeated auth rejections (cert expired/revoked).
func (c *GRPCClient) NeedsCertReEnrollment() bool {
	return c.consecutiveFailures.Load() >= 5
}

// ForceReEnrollment deletes existing certs so the next connection attempt
// triggers fresh enrollment via the TOFU flow.
func (c *GRPCClient) ForceReEnrollment() error {
	files := []string{c.config.CertFile, c.config.KeyFile}
	for _, f := range files {
		if err := os.Remove(f); err != nil && !os.IsNotExist(err) {
			return fmt.Errorf("remove %s: %w", f, err)
		}
	}
	c.needsCerts = true
	c.consecutiveFailures.Store(0)
	log.Println("[gRPC] Deleted client certs for re-enrollment — next connect will request new certs")
	return nil
}

// NewGRPCClient creates a new gRPC client. version is the agent's build version.
func NewGRPCClient(ctx context.Context, cfg *config.Config, version string) (*GRPCClient, error) {
	hostname, err := os.Hostname()
	if err != nil {
		log.Printf("[gRPC] WARNING: failed to get hostname: %v", err)
		hostname = "unknown"
	}

	client := &GRPCClient{
		hostname:     hostname,
		version:      version,
		config:       cfg,
		httpEndpoint: cfg.HTTPEndpoint,
		HealCmds:     make(chan *pb.HealCommand, HealChanSize),
	}

	if err := client.connect(ctx); err != nil {
		// Return the client even on failure so reconnectLoop can retry
		return client, err
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
		// No certs yet — connect with TLS for certificate enrollment.
		// Use TOFU (Trust On First Use): accept the server cert on first
		// connection and pin its SHA-256 fingerprint. On subsequent
		// connections (reconnect/renewal), verify the pinned fingerprint.
		fpPath := certFingerprintPath(c.config.DataDir)
		enrollTLS := &tls.Config{
			InsecureSkipVerify: true, // We verify manually via VerifyConnection
			VerifyConnection: func(cs tls.ConnectionState) error {
				if len(cs.PeerCertificates) == 0 {
					return fmt.Errorf("no peer certificates presented")
				}
				actual := sha256.Sum256(cs.PeerCertificates[0].Raw)
				pinned, pinErr := loadCertFingerprint(fpPath)
				if pinErr != nil {
					// First connection — TOFU: accept and pin
					log.Printf("[transport] TOFU: pinning appliance cert fingerprint: %x", actual)
					if saveErr := saveCertFingerprint(fpPath, actual[:]); saveErr != nil {
						log.Printf("[transport] WARNING: failed to save cert pin: %v", saveErr)
					}
					c.pinMismatchCount.Store(0)
					return nil
				}
				// Subsequent connection — verify pin
				if !bytes.Equal(actual[:], pinned) {
					n := c.pinMismatchCount.Add(1)
					log.Printf("[transport] TOFU pin mismatch #%d — expected %x, got %x", n, pinned, actual[:])
					return fmt.Errorf("%w — expected %x, got %x (attempt %d)", ErrPinMismatch, pinned, actual[:], n)
				}
				// Pin matches — reset mismatch counter
				c.pinMismatchCount.Store(0)
				return nil
			},
		}
		log.Println("[gRPC] No TLS certs found, connecting with TLS (TOFU pinning) for enrollment")
		opts = append(opts, grpc.WithTransportCredentials(credentials.NewTLS(enrollTLS)))
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
		AgentVersion:      c.version,
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
		log.Printf("[gRPC] Reconnected with mTLS (agent_id=%s)", c.agentID)
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
//
// Captures stream and done references once at start to avoid mutex contention
// with Close()/Reconnect()/StartDriftStream() — prevents deadlock where Close
// holds Lock while waiting for streamDone, and this goroutine tries to RLock.
func (c *GRPCClient) readDriftAcks() {
	// Capture references once — never re-read under lock during the loop
	c.mu.RLock()
	stream := c.driftStream
	done := c.streamDone
	c.mu.RUnlock()

	defer close(done)

	if stream == nil {
		return
	}

	defer func() {
		// Mark stream as inactive so SendDrift falls back to one-shot.
		// Only nil our own stream, not a replacement set by StartDriftStream.
		c.mu.Lock()
		if c.driftStream == stream {
			c.driftStream = nil
		}
		c.mu.Unlock()
	}()

	for {
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
				if len(c.HealCmds) > cap(c.HealCmds)*3/4 {
					log.Printf("[gRPC] WARNING: heal channel at %d/%d capacity", len(c.HealCmds), cap(c.HealCmds))
				}
			default:
				log.Printf("[gRPC] CRITICAL: heal channel full (%d/%d), dropping command %s", len(c.HealCmds), cap(c.HealCmds), cmd.CommandId)
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
		AgentId:      c.agentID,
		Timestamp:    time.Now().Unix(),
		AgentVersion: c.version,
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
			if len(c.HealCmds) > cap(c.HealCmds)*3/4 {
				log.Printf("[gRPC] WARNING: heal channel at %d/%d capacity", len(c.HealCmds), cap(c.HealCmds))
			}
		default:
			log.Printf("[gRPC] CRITICAL: heal channel full (%d/%d), dropping command %s", len(c.HealCmds), cap(c.HealCmds), cmd.CommandId)
		}
	}

	return resp, nil
}

// CertNeedsRenewal checks if the agent cert expires within 30 days.
// Returns true if cert should be renewed.
func (c *GRPCClient) CertNeedsRenewal() bool {
	certPEM, err := os.ReadFile(c.config.CertFile)
	if err != nil {
		return false // no cert = will be enrolled fresh
	}
	block, _ := pem.Decode(certPEM)
	if block == nil {
		return true
	}
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return true
	}
	// Renew if less than 30 days remain
	return time.Until(cert.NotAfter) < 30*24*time.Hour
}

// RenewCerts triggers re-enrollment by setting needsCerts and re-registering.
// This closes the current connection and reconnects with new certs.
func (c *GRPCClient) RenewCerts(ctx context.Context) error {
	log.Println("[gRPC] Certificate renewal triggered — re-enrolling")
	c.mu.Lock()
	c.needsCerts = true
	c.mu.Unlock()

	// Re-register will detect needsCerts and request new certs from server
	_, err := c.Register(ctx)
	if err != nil {
		return fmt.Errorf("cert renewal failed: %w", err)
	}
	log.Println("[gRPC] Certificate renewal complete")
	return nil
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

// MarkDisconnected sets the connection state to disconnected so the reconnect
// loop can detect it and trigger a full reconnect. Called by the heartbeat loop
// after consecutive failures indicate the server is unreachable.
func (c *GRPCClient) MarkDisconnected() {
	c.mu.Lock()
	c.connected = false
	c.mu.Unlock()
}

// GetAgentID returns the registered agent ID
func (c *GRPCClient) GetAgentID() string {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.agentID
}

// Close closes the gRPC connection.
// Releases the mutex before waiting for the ack reader to prevent deadlock.
func (c *GRPCClient) Close() error {
	// Grab stream references and nil them under lock
	c.mu.Lock()
	stream := c.driftStream
	done := c.streamDone
	c.driftStream = nil
	c.mu.Unlock()

	// Signal stream closure outside of lock (ack reader may hold RLock during Recv)
	if stream != nil {
		stream.CloseSend()
	}

	// Wait for ack reader to drain remaining acks and exit
	if done != nil {
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			log.Println("[gRPC] Timed out waiting for drift ack reader to exit")
		}
	}

	c.mu.Lock()
	defer c.mu.Unlock()

	if c.conn != nil {
		return c.conn.Close()
	}
	return nil
}

// Reconnect attempts to reconnect to the appliance.
// Releases the mutex before waiting for the ack reader to prevent deadlock.
func (c *GRPCClient) Reconnect(ctx context.Context) error {
	// Close stream outside of lock to avoid deadlock with ack reader
	c.mu.Lock()
	stream := c.driftStream
	done := c.streamDone
	c.driftStream = nil
	c.mu.Unlock()

	if stream != nil {
		stream.CloseSend()
	}

	if done != nil {
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			log.Println("[gRPC] Timed out waiting for drift ack reader to exit")
		}
	}

	c.mu.Lock()
	defer c.mu.Unlock()

	c.streamDone = nil

	if c.conn != nil {
		c.conn.Close()
	}
	c.connected = false
	c.agentID = ""

	return c.connect(ctx)
}

// PinMismatchCount returns the current consecutive pin mismatch count.
func (c *GRPCClient) PinMismatchCount() int {
	return int(c.pinMismatchCount.Load())
}

// ClearPinFile removes the TOFU certificate pin file so the next connection
// will re-pin via TOFU. This is used for auto-recovery when the appliance
// regenerates its self-signed TLS certificate (e.g., after daemon restart).
func (c *GRPCClient) ClearPinFile() error {
	fpPath := certFingerprintPath(c.config.DataDir)
	if err := os.Remove(fpPath); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("failed to remove pin file: %w", err)
	}
	c.pinMismatchCount.Store(0)
	log.Printf("[transport] TOFU pin file cleared — next connection will re-enroll")
	return nil
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

// certFingerprintPath returns the path to the pinned cert fingerprint file.
func certFingerprintPath(dataDir string) string {
	return filepath.Join(dataDir, "appliance_cert_pin.hex")
}

// saveCertFingerprint writes a SHA-256 fingerprint to disk as hex.
func saveCertFingerprint(path string, fingerprint []byte) error {
	return os.WriteFile(path, []byte(hex.EncodeToString(fingerprint)), 0600)
}

// loadCertFingerprint reads a previously pinned SHA-256 fingerprint.
func loadCertFingerprint(path string) ([]byte, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return hex.DecodeString(strings.TrimSpace(string(data)))
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
