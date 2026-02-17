//go:build !windows

// Package eventlog provides a stub for non-Windows systems.
package eventlog

import (
	"fmt"
	"time"

	pb "github.com/osiriscare/agent/proto"
)

// ComplianceEvent represents a detected compliance-relevant event
type ComplianceEvent struct {
	CheckType    string
	Passed       bool
	Expected     string
	Actual       string
	HIPAAControl string
	EventID      uint32
	Channel      string
	Timestamp    time.Time
}

// EventCallback is called when a compliance event is detected
type EventCallback func(event *ComplianceEvent)

// Watcher is a stub on non-Windows systems
type Watcher struct {
	running bool
}

// NewWatcher creates a stub watcher on non-Windows
func NewWatcher(hostname string, callback EventCallback) *Watcher {
	return &Watcher{}
}

// Start is a no-op on non-Windows
func (w *Watcher) Start() error {
	return nil
}

// Stop is a no-op on non-Windows
func (w *Watcher) Stop() {}

// IsRunning returns false on non-Windows
func (w *Watcher) IsRunning() bool {
	return false
}

// ConvertToDriftEvent converts a ComplianceEvent to a protobuf DriftEvent
func (e *ComplianceEvent) ConvertToDriftEvent(agentID, hostname string) *pb.DriftEvent {
	return &pb.DriftEvent{
		AgentId:      agentID,
		Hostname:     hostname,
		CheckType:    e.CheckType,
		Passed:       e.Passed,
		Expected:     e.Expected,
		Actual:       e.Actual,
		HipaaControl: e.HIPAAControl,
		Timestamp:    e.Timestamp.Unix(),
		Metadata: map[string]string{
			"source":    "eventlog",
			"channel":   e.Channel,
			"event_id":  fmt.Sprintf("%d", e.EventID),
			"real_time": "true",
		},
	}
}
