package grpcserver

import (
	"context"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/keepalive"

	"github.com/osiriscare/appliance/internal/ca"
	pb "github.com/osiriscare/appliance/proto"
)

// HealRequest is sent over the HealChan when drift needs healing.
type HealRequest struct {
	AgentID      string
	Hostname     string
	CheckType    string
	HIPAAControl string
	Expected     string
	Actual       string
	Metadata     map[string]string
}

// Config holds gRPC server configuration.
type Config struct {
	Port        int
	TLSCertFile string
	TLSKeyFile  string
	CACertFile  string
	SiteID      string
}

// Server wraps the gRPC server and all its dependencies.
type Server struct {
	config   Config
	registry *AgentRegistry
	agentCA  *ca.AgentCA
	grpc     *grpc.Server

	// HealChan receives incidents that need healing. The Python daemon
	// (or later, the Go daemon) reads from this channel.
	HealChan chan HealRequest
}

// NewServer creates a new gRPC server.
func NewServer(cfg Config, registry *AgentRegistry, agentCA *ca.AgentCA) *Server {
	return &Server{
		config:   cfg,
		registry: registry,
		agentCA:  agentCA,
		HealChan: make(chan HealRequest, 256),
	}
}

// Serve starts the gRPC server and blocks until stopped.
func (s *Server) Serve() error {
	lis, err := net.Listen("tcp", fmt.Sprintf(":%d", s.config.Port))
	if err != nil {
		return fmt.Errorf("listen on port %d: %w", s.config.Port, err)
	}

	opts := []grpc.ServerOption{
		grpc.KeepaliveParams(keepalive.ServerParameters{
			Time:    30 * time.Second,
			Timeout: 10 * time.Second,
		}),
		grpc.KeepaliveEnforcementPolicy(keepalive.EnforcementPolicy{
			MinTime:             10 * time.Second,
			PermitWithoutStream: true,
		}),
		grpc.MaxConcurrentStreams(100),
	}

	// Load TLS credentials
	tlsCreds, err := s.loadTLS()
	if err != nil {
		log.Printf("[gRPC] No TLS configured (%v), starting insecure", err)
	} else if tlsCreds != nil {
		opts = append(opts, grpc.Creds(tlsCreds))
		log.Printf("[gRPC] TLS enabled")
	}

	if s.agentCA == nil {
		log.Println("[gRPC] WARNING: starting without agent_ca — certificate enrollment disabled")
	}

	s.grpc = grpc.NewServer(opts...)
	pb.RegisterComplianceAgentServer(s.grpc, &servicer{
		registry: s.registry,
		agentCA:  s.agentCA,
		healChan: s.HealChan,
		siteID:   s.config.SiteID,
	})

	log.Printf("[gRPC] Listening on :%d", s.config.Port)
	return s.grpc.Serve(lis)
}

// GracefulStop stops the gRPC server gracefully.
func (s *Server) GracefulStop() {
	if s.grpc != nil {
		s.grpc.GracefulStop()
	}
}

func (s *Server) loadTLS() (credentials.TransportCredentials, error) {
	if s.config.TLSCertFile == "" || s.config.TLSKeyFile == "" {
		return nil, fmt.Errorf("no TLS cert/key configured")
	}

	if _, err := os.Stat(s.config.TLSCertFile); os.IsNotExist(err) {
		return nil, fmt.Errorf("TLS cert not found: %s", s.config.TLSCertFile)
	}
	if _, err := os.Stat(s.config.TLSKeyFile); os.IsNotExist(err) {
		return nil, fmt.Errorf("TLS key not found: %s", s.config.TLSKeyFile)
	}

	cert, err := tls.LoadX509KeyPair(s.config.TLSCertFile, s.config.TLSKeyFile)
	if err != nil {
		return nil, fmt.Errorf("load TLS cert: %w", err)
	}

	tlsConfig := &tls.Config{
		Certificates: []tls.Certificate{cert},
		MinVersion:   tls.VersionTLS12,
	}

	// Load CA for client certificate verification (mTLS)
	if s.config.CACertFile != "" {
		caCert, err := os.ReadFile(s.config.CACertFile)
		if err == nil {
			pool := x509.NewCertPool()
			if pool.AppendCertsFromPEM(caCert) {
				tlsConfig.ClientCAs = pool
				tlsConfig.ClientAuth = tls.VerifyClientCertIfGiven
			}
		}
	}

	return credentials.NewTLS(tlsConfig), nil
}

// servicer implements the ComplianceAgent gRPC service.
type servicer struct {
	pb.UnimplementedComplianceAgentServer
	registry *AgentRegistry
	agentCA  *ca.AgentCA
	healChan chan HealRequest
	siteID   string
}

// checkTypeMap maps Go agent check types to L1 rule check types.
var checkTypeMap = map[string]string{
	"defender":   "windows_defender",
	"firewall":   "firewall_status",
	"screenlock": "screen_lock",
	"patches":    "patching",
}

// healMap defines immediate heal actions for known check types.
var healMap = map[string]struct {
	Action  string
	Timeout int64
}{
	"firewall":   {"enable", 60},
	"defender":   {"start", 60},
	"bitlocker":  {"enable", 120},
	"screenlock": {"configure", 30},
}

func (s *servicer) Register(_ context.Context, req *pb.RegisterRequest) (*pb.RegisterResponse, error) {
	log.Printf("[gRPC] Agent registration: %s (needs_certs=%v)", req.Hostname, req.NeedsCertificates)

	agentID := fmt.Sprintf("go-%s-%s", req.Hostname, randomHex(8))

	now := time.Now().UTC()
	state := &AgentState{
		AgentID:       agentID,
		Hostname:      req.Hostname,
		hostnameLower: toLower(req.Hostname),
		Tier:          pb.CapabilityTier_MONITOR_ONLY,
		ConnectedAt:   now,
		LastHeartbeat: now,
	}
	s.registry.Register(state)

	if len(req.InstalledSoftware) > 0 {
		max := 5
		if len(req.InstalledSoftware) < max {
			max = len(req.InstalledSoftware)
		}
		log.Printf("[gRPC] Agent %s software: %v", req.Hostname, req.InstalledSoftware[:max])
	}

	// Issue certificates if requested
	var caCertPEM, agentCertPEM, agentKeyPEM []byte
	if req.NeedsCertificates && s.agentCA != nil {
		certPEM, keyPEM, caPEM, err := s.agentCA.IssueAgentCert(req.Hostname, agentID)
		if err != nil {
			log.Printf("[gRPC] Failed to issue certs for %s: %v", req.Hostname, err)
		} else {
			caCertPEM = caPEM
			agentCertPEM = certPEM
			agentKeyPEM = keyPEM
			log.Printf("[gRPC] Issued certificates for %s", req.Hostname)
		}
	} else if req.NeedsCertificates && s.agentCA == nil {
		log.Printf("[gRPC] WARNING: Agent %s requested certificates but agent_ca is not configured", req.Hostname)
	}

	return &pb.RegisterResponse{
		AgentId:              agentID,
		CheckIntervalSeconds: 300,
		EnabledChecks: []string{
			"bitlocker", "defender", "patches",
			"firewall", "screenlock", "rmm_detection",
		},
		CapabilityTier: pb.CapabilityTier_MONITOR_ONLY,
		CheckConfig:    map[string]string{},
		CaCertPem:      caCertPEM,
		AgentCertPem:   agentCertPEM,
		AgentKeyPem:    agentKeyPEM,
	}, nil
}

func (s *servicer) ReportDrift(stream pb.ComplianceAgent_ReportDriftServer) error {
	for {
		event, err := stream.Recv()
		if err != nil {
			if err == io.EOF {
				return nil
			}
			return err
		}

		log.Printf("[gRPC] Drift: %s/%s passed=%v", event.Hostname, event.CheckType, event.Passed)

		// Update agent stats
		if agent := s.registry.GetAgent(event.AgentId); agent != nil {
			agent.DriftCount++
			agent.LastHeartbeat = time.Now().UTC()
		}

		// Build immediate heal command for failed checks
		var healCmd *pb.HealCommand
		if !event.Passed {
			if spec, ok := healMap[event.CheckType]; ok {
				healCmd = &pb.HealCommand{
					CommandId:      fmt.Sprintf("drift-heal-%s", randomHex(12)),
					CheckType:      event.CheckType,
					Action:         spec.Action,
					Params:         map[string]string{},
					TimeoutSeconds: spec.Timeout,
				}
				log.Printf("[gRPC] Immediate heal for %s: %s/%s (id=%s)",
					event.Hostname, event.CheckType, spec.Action, healCmd.CommandId)
			}

			// Route to healing engine
			s.routeDriftToHealing(event)
		}

		if err := stream.Send(&pb.DriftAck{
			EventId:     fmt.Sprintf("%s-%d", event.AgentId, event.Timestamp),
			Received:    true,
			HealCommand: healCmd,
		}); err != nil {
			return err
		}
	}
}

func (s *servicer) ReportHealing(_ context.Context, req *pb.HealingResult) (*pb.HealingAck, error) {
	log.Printf("[gRPC] Healing: %s/%s success=%v", req.Hostname, req.CheckType, req.Success)

	if req.Artifacts != nil {
		if _, ok := req.Artifacts["recovery_key"]; ok {
			log.Printf("[gRPC] Storing BitLocker recovery key for %s", req.Hostname)
		}
	}

	return &pb.HealingAck{
		EventId:  fmt.Sprintf("%s-%d", req.AgentId, req.Timestamp),
		Received: true,
	}, nil
}

func (s *servicer) Heartbeat(_ context.Context, req *pb.HeartbeatRequest) (*pb.HeartbeatResponse, error) {
	if agent := s.registry.GetAgent(req.AgentId); agent != nil {
		agent.LastHeartbeat = time.Now().UTC()
	}

	pending := s.registry.PopPendingCommands(req.AgentId)
	if len(pending) > 0 {
		log.Printf("[gRPC] Delivering %d heal commands to %s via heartbeat", len(pending), req.AgentId)
	}

	return &pb.HeartbeatResponse{
		Acknowledged:    true,
		ConfigChanged:   false,
		PendingCommands: pending,
	}, nil
}

func (s *servicer) ReportRMMStatus(_ context.Context, req *pb.RMMStatusReport) (*pb.RMMAck, error) {
	log.Printf("[gRPC] RMM status from %s: %d agents", req.Hostname, len(req.DetectedAgents))

	for _, agent := range req.DetectedAgents {
		log.Printf("[gRPC]   - %s v%s running=%v", agent.Name, agent.Version, agent.Running)
	}

	if state := s.registry.GetAgent(req.AgentId); state != nil {
		state.RMMAgents = req.DetectedAgents
		state.LastHeartbeat = time.Now().UTC()
	}

	return &pb.RMMAck{Received: true}, nil
}

// routeDriftToHealing sends a drift event to the healing channel.
func (s *servicer) routeDriftToHealing(event *pb.DriftEvent) {
	mapped := event.CheckType
	if m, ok := checkTypeMap[event.CheckType]; ok {
		mapped = m
	}

	select {
	case s.healChan <- HealRequest{
		AgentID:      event.AgentId,
		Hostname:     event.Hostname,
		CheckType:    mapped,
		HIPAAControl: event.HipaaControl,
		Expected:     event.Expected,
		Actual:       event.Actual,
		Metadata:     event.Metadata,
	}:
	default:
		log.Printf("[gRPC] WARNING: heal channel full, dropping drift event for %s/%s",
			event.Hostname, event.CheckType)
	}
}

func randomHex(n int) string {
	b := make([]byte, n)
	_, _ = rand.Read(b)
	const hexChars = "0123456789abcdef"
	s := make([]byte, n)
	for i := range s {
		s[i] = hexChars[b[i]%16]
	}
	return string(s)
}
