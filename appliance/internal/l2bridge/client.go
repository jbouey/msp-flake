// Package l2bridge provides a Unix socket JSON-RPC client for the Python L2 sidecar.
//
// The Go daemon sends incidents that L1 couldn't match to the Python L2 LLM planner
// via a persistent Unix domain socket connection. This avoids ~200ms Python startup
// overhead per invocation.
//
// Protocol: JSON-RPC 2.0 over Unix socket at /var/lib/msp/l2.sock
// Methods: "plan" (primary), "health" (liveness check)
package l2bridge

import (
	"bufio"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"sync"
	"sync/atomic"
	"time"
)

// Incident is the input to the L2 planner (matches Python Incident dataclass).
type Incident struct {
	ID               string                 `json:"id"`
	SiteID           string                 `json:"site_id"`
	HostID           string                 `json:"host_id"`
	IncidentType     string                 `json:"incident_type"`
	Severity         string                 `json:"severity"`
	RawData          map[string]interface{} `json:"raw_data"`
	PatternSignature string                 `json:"pattern_signature"`
	CreatedAt        string                 `json:"created_at"`
}

// LLMDecision is the output from the L2 planner.
type LLMDecision struct {
	IncidentID        string                 `json:"incident_id"`
	RecommendedAction string                 `json:"recommended_action"`
	ActionParams      map[string]interface{} `json:"action_params"`
	Confidence        float64                `json:"confidence"`
	Reasoning         string                 `json:"reasoning"`
	RunbookID         string                 `json:"runbook_id,omitempty"`
	RequiresApproval  bool                   `json:"requires_approval"`
	EscalateToL3      bool                   `json:"escalate_to_l3"`
	ContextUsed       map[string]interface{} `json:"context_used,omitempty"`
}

// ShouldExecute returns true if the decision can be auto-executed (no escalation needed).
func (d *LLMDecision) ShouldExecute() bool {
	return !d.EscalateToL3 && !d.RequiresApproval && d.Confidence >= 0.6
}

// jsonRPCRequest is a JSON-RPC 2.0 request.
type jsonRPCRequest struct {
	JSONRPC string      `json:"jsonrpc"`
	Method  string      `json:"method"`
	Params  interface{} `json:"params"`
	ID      int64       `json:"id"`
}

// jsonRPCResponse is a JSON-RPC 2.0 response.
type jsonRPCResponse struct {
	JSONRPC string          `json:"jsonrpc"`
	Result  json.RawMessage `json:"result,omitempty"`
	Error   *jsonRPCError   `json:"error,omitempty"`
	ID      int64           `json:"id"`
}

// jsonRPCError is a JSON-RPC 2.0 error.
type jsonRPCError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

// Client is the L2 bridge client that connects to the Python sidecar.
type Client struct {
	socketPath string
	timeout    time.Duration
	conn       net.Conn
	reader     *bufio.Reader
	mu         sync.Mutex
	reqID      atomic.Int64
}

// NewClient creates a new L2 bridge client.
func NewClient(socketPath string, timeout time.Duration) *Client {
	if timeout == 0 {
		timeout = 30 * time.Second
	}
	return &Client{
		socketPath: socketPath,
		timeout:    timeout,
	}
}

// Connect establishes a connection to the Python L2 sidecar.
func (c *Client) Connect() error {
	c.mu.Lock()
	defer c.mu.Unlock()

	if c.conn != nil {
		c.conn.Close()
	}

	conn, err := net.DialTimeout("unix", c.socketPath, 5*time.Second)
	if err != nil {
		return fmt.Errorf("connect to L2 sidecar at %s: %w", c.socketPath, err)
	}

	c.conn = conn
	c.reader = bufio.NewReader(conn)
	log.Printf("[l2bridge] Connected to %s", c.socketPath)
	return nil
}

// Close closes the connection.
func (c *Client) Close() {
	c.mu.Lock()
	defer c.mu.Unlock()

	if c.conn != nil {
		c.conn.Close()
		c.conn = nil
		c.reader = nil
	}
}

// IsConnected returns true if the client has an active connection.
func (c *Client) IsConnected() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.conn != nil
}

// Plan sends an incident to the L2 planner and returns the decision.
func (c *Client) Plan(incident *Incident) (*LLMDecision, error) {
	resp, err := c.call("plan", incident)
	if err != nil {
		return nil, fmt.Errorf("L2 plan: %w", err)
	}

	var decision LLMDecision
	if err := json.Unmarshal(resp, &decision); err != nil {
		return nil, fmt.Errorf("parse L2 decision: %w", err)
	}

	return &decision, nil
}

// Health checks if the sidecar is alive.
func (c *Client) Health() error {
	resp, err := c.call("health", nil)
	if err != nil {
		return fmt.Errorf("L2 health: %w", err)
	}

	var result map[string]interface{}
	if err := json.Unmarshal(resp, &result); err != nil {
		return fmt.Errorf("parse health: %w", err)
	}

	status, _ := result["status"].(string)
	if status != "ok" {
		return fmt.Errorf("L2 sidecar unhealthy: %s", status)
	}

	return nil
}

// PlanWithRetry attempts to plan with automatic reconnection on failure.
func (c *Client) PlanWithRetry(incident *Incident, maxRetries int) (*LLMDecision, error) {
	var lastErr error
	for attempt := 0; attempt <= maxRetries; attempt++ {
		if attempt > 0 {
			log.Printf("[l2bridge] Retry %d/%d after error: %v", attempt, maxRetries, lastErr)
			time.Sleep(time.Duration(attempt) * time.Second)

			if err := c.Connect(); err != nil {
				lastErr = err
				continue
			}
		}

		decision, err := c.Plan(incident)
		if err == nil {
			return decision, nil
		}
		lastErr = err
	}

	return nil, fmt.Errorf("L2 plan failed after %d retries: %w", maxRetries, lastErr)
}

// call sends a JSON-RPC request and returns the result.
func (c *Client) call(method string, params interface{}) (json.RawMessage, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	if c.conn == nil {
		return nil, fmt.Errorf("not connected to L2 sidecar")
	}

	id := c.reqID.Add(1)

	req := jsonRPCRequest{
		JSONRPC: "2.0",
		Method:  method,
		Params:  params,
		ID:      id,
	}

	data, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("marshal request: %w", err)
	}

	// Set deadline for the entire request/response cycle
	if err := c.conn.SetDeadline(time.Now().Add(c.timeout)); err != nil {
		return nil, fmt.Errorf("set deadline: %w", err)
	}

	// Write request followed by newline (line-delimited JSON)
	data = append(data, '\n')
	if _, err := c.conn.Write(data); err != nil {
		c.conn.Close()
		c.conn = nil
		c.reader = nil
		return nil, fmt.Errorf("write request: %w", err)
	}

	// Read response (line-delimited)
	line, err := c.reader.ReadBytes('\n')
	if err != nil {
		c.conn.Close()
		c.conn = nil
		c.reader = nil
		return nil, fmt.Errorf("read response: %w", err)
	}

	var resp jsonRPCResponse
	if err := json.Unmarshal(line, &resp); err != nil {
		return nil, fmt.Errorf("parse response: %w", err)
	}

	if resp.Error != nil {
		return nil, fmt.Errorf("RPC error %d: %s", resp.Error.Code, resp.Error.Message)
	}

	return resp.Result, nil
}
