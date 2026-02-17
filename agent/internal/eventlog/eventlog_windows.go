//go:build windows

// Package eventlog provides real-time Windows Event Log monitoring for compliance events.
// This replaces polling with instant detection (<1 second vs 5 minutes).
package eventlog

import (
	"context"
	"fmt"
	"log"
	"strings"
	"sync"
	"syscall"
	"time"
	"unsafe"

	pb "github.com/osiriscare/agent/proto"
)

// Windows Event Log API
var (
	wevtapi                    = syscall.NewLazyDLL("wevtapi.dll")
	procEvtSubscribe           = wevtapi.NewProc("EvtSubscribe")
	procEvtClose               = wevtapi.NewProc("EvtClose")
	procEvtRender              = wevtapi.NewProc("EvtRender")
	procEvtCreateRenderContext = wevtapi.NewProc("EvtCreateRenderContext")
)

// Event subscription flags
const (
	EvtSubscribeToFutureEvents      = 1
	EvtSubscribeStartAtOldestRecord = 2
	EvtRenderEventValues            = 0
	EvtRenderEventXml               = 1
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

// Watcher monitors Windows Event Log for compliance-relevant events
type Watcher struct {
	subscriptions []uintptr
	callback      EventCallback
	hostname      string
	mu            sync.Mutex
	running       bool
	ctx           context.Context
	cancel        context.CancelFunc
}

// EventChannel defines which events to watch in a specific channel
type EventChannel struct {
	Name   string   // e.g., "Microsoft-Windows-Windows Firewall With Advanced Security/Firewall"
	Query  string   // XPath query for filtering events
	Events []uint32 // Event IDs to watch
}

// ComplianceChannels defines all channels to monitor for HIPAA compliance
var ComplianceChannels = []EventChannel{
	// Firewall events
	{
		Name:   "Microsoft-Windows-Windows Firewall With Advanced Security/Firewall",
		Query:  "*[System[(EventID=2003 or EventID=2004 or EventID=2005 or EventID=2006)]]",
		Events: []uint32{2003, 2004, 2005, 2006}, // Firewall profile/rule changes
	},
	// Windows Defender events
	{
		Name:   "Microsoft-Windows-Windows Defender/Operational",
		Query:  "*[System[(EventID=5001 or EventID=5010 or EventID=5012)]]",
		Events: []uint32{5001, 5010, 5012}, // Defender disabled/config changes
	},
	// Security events (failed logins, lockouts, privilege use)
	{
		Name:   "Security",
		Query:  "*[System[(EventID=4625 or EventID=4740 or EventID=4672 or EventID=4719)]]",
		Events: []uint32{4625, 4740, 4672, 4719},
	},
	// System events (service state changes)
	{
		Name:   "System",
		Query:  "*[System[(EventID=7036 or EventID=7040)]]",
		Events: []uint32{7036, 7040}, // Service start/stop, startup type change
	},
	// BitLocker events
	{
		Name:   "Microsoft-Windows-BitLocker/BitLocker Management",
		Query:  "*[System[(EventID=24620 or EventID=24621)]]",
		Events: []uint32{24620, 24621}, // Protection status changes
	},
}

// NewWatcher creates a new event log watcher
func NewWatcher(hostname string, callback EventCallback) *Watcher {
	ctx, cancel := context.WithCancel(context.Background())
	return &Watcher{
		subscriptions: make([]uintptr, 0),
		callback:      callback,
		hostname:      hostname,
		ctx:           ctx,
		cancel:        cancel,
	}
}

// Start begins monitoring event logs
func (w *Watcher) Start() error {
	w.mu.Lock()
	defer w.mu.Unlock()

	if w.running {
		return nil
	}

	log.Println("[EventLog] Starting Windows Event Log monitoring...")

	for _, channel := range ComplianceChannels {
		if err := w.subscribeChannel(channel); err != nil {
			log.Printf("[EventLog] Warning: Failed to subscribe to %s: %v", channel.Name, err)
			// Continue with other channels
		} else {
			log.Printf("[EventLog] Subscribed to: %s", channel.Name)
		}
	}

	w.running = true
	log.Printf("[EventLog] Monitoring %d event channels for real-time compliance detection", len(w.subscriptions))
	return nil
}

// subscribeChannel subscribes to events from a specific channel
func (w *Watcher) subscribeChannel(channel EventChannel) error {
	channelPath, err := syscall.UTF16PtrFromString(channel.Name)
	if err != nil {
		return err
	}

	query, err := syscall.UTF16PtrFromString(channel.Query)
	if err != nil {
		return err
	}

	// Create callback context
	callbackData := &channelCallbackData{
		watcher: w,
		channel: channel,
	}

	// Subscribe to events
	handle, _, callErr := procEvtSubscribe.Call(
		0,                                      // Session (0 = local)
		0,                                      // SignalEvent (not used with callback)
		uintptr(unsafe.Pointer(channelPath)),   // ChannelPath
		uintptr(unsafe.Pointer(query)),         // Query
		0,                                      // Bookmark (not used)
		uintptr(unsafe.Pointer(callbackData)),  // Context for callback
		syscall.NewCallback(eventCallback),     // Callback function
		uintptr(EvtSubscribeToFutureEvents),    // Flags
	)

	if handle == 0 {
		return callErr
	}

	w.subscriptions = append(w.subscriptions, handle)
	return nil
}

// channelCallbackData is passed to the Windows callback
type channelCallbackData struct {
	watcher *Watcher
	channel EventChannel
}

// eventCallback is the Windows event callback function
func eventCallback(action, userContext, event uintptr) uintptr {
	if action != 1 { // EvtSubscribeActionDeliver
		return 0
	}

	data := (*channelCallbackData)(unsafe.Pointer(userContext))
	if data == nil || data.watcher == nil {
		return 0
	}

	// Parse the event and check if it's compliance-relevant
	compEvent := data.watcher.parseEvent(event, data.channel)
	if compEvent != nil && data.watcher.callback != nil {
		data.watcher.callback(compEvent)
	}

	return 0
}

// parseEvent extracts compliance information from a Windows event XML
func (w *Watcher) parseEvent(eventHandle uintptr, channel EventChannel) *ComplianceEvent {
	// Render event as XML
	var bufferSize uint32 = 65536
	buffer := make([]uint16, bufferSize)
	var bufferUsed, propertyCount uint32

	ret, _, _ := procEvtRender.Call(
		0, // Context
		eventHandle,
		uintptr(EvtRenderEventXml),
		uintptr(bufferSize*2),
		uintptr(unsafe.Pointer(&buffer[0])),
		uintptr(unsafe.Pointer(&bufferUsed)),
		uintptr(unsafe.Pointer(&propertyCount)),
	)

	if ret == 0 {
		// Failed to render — fall back to channel-based event
		return w.createEventFromChannel(channel, 0)
	}

	// Convert UTF-16 buffer to string
	charCount := bufferUsed / 2
	if charCount > bufferSize {
		charCount = bufferSize
	}
	xml := syscall.UTF16ToString(buffer[:charCount])

	// Extract EventID from XML: <EventID>NNNN</EventID>
	eventID := extractXMLValue(xml, "EventID")
	var eid uint32
	if eventID != "" {
		fmt.Sscanf(eventID, "%d", &eid)
	}

	return w.createEventFromChannel(channel, eid)
}

// extractXMLValue extracts the text content of a simple XML element.
// e.g. extractXMLValue(xml, "EventID") returns "5001" from "<EventID>5001</EventID>"
func extractXMLValue(xml, tag string) string {
	openTag := "<" + tag
	closeTag := "</" + tag + ">"

	start := strings.Index(xml, openTag)
	if start < 0 {
		return ""
	}
	// Skip past the opening tag (handles attributes like <EventID Qualifiers='0'>)
	gt := strings.Index(xml[start:], ">")
	if gt < 0 {
		return ""
	}
	contentStart := start + gt + 1

	end := strings.Index(xml[contentStart:], closeTag)
	if end < 0 {
		return ""
	}
	return strings.TrimSpace(xml[contentStart : contentStart+end])
}

// createEventFromChannel creates a ComplianceEvent with event-ID-specific details
func (w *Watcher) createEventFromChannel(channel EventChannel, eventID uint32) *ComplianceEvent {
	event := &ComplianceEvent{
		Channel:   channel.Name,
		EventID:   eventID,
		Timestamp: time.Now(),
		Passed:    false,
	}

	// Use EventID for specific messaging when available
	switch {
	case strings.Contains(channel.Name, "Firewall"):
		event.CheckType = "firewall"
		event.HIPAAControl = "164.312(e)(1)"
		switch eventID {
		case 2003:
			event.Expected = "Firewall profile unchanged"
			event.Actual = "Firewall profile setting changed"
		case 2004:
			event.Expected = "No new firewall rules"
			event.Actual = "Firewall rule added"
		case 2005:
			event.Expected = "Firewall rules unchanged"
			event.Actual = "Firewall rule modified"
		case 2006:
			event.Expected = "Firewall rules intact"
			event.Actual = "Firewall rule deleted"
		default:
			event.Expected = "Firewall enabled"
			event.Actual = "Firewall configuration changed"
		}

	case strings.Contains(channel.Name, "Defender"):
		event.CheckType = "defender"
		event.HIPAAControl = "164.308(a)(5)(ii)(B)"
		switch eventID {
		case 5001:
			event.Expected = "Real-time protection enabled"
			event.Actual = "Real-time protection disabled"
		case 5010:
			event.Expected = "Scan enabled"
			event.Actual = "Antispyware scanning disabled"
		case 5012:
			event.Expected = "Antimalware active"
			event.Actual = "Antimalware engine disabled"
		default:
			event.Expected = "Defender protection active"
			event.Actual = "Defender configuration changed"
		}

	case strings.Contains(channel.Name, "BitLocker"):
		event.CheckType = "bitlocker"
		event.HIPAAControl = "164.312(a)(2)(iv)"
		switch eventID {
		case 24620:
			event.Expected = "BitLocker protection on"
			event.Actual = "BitLocker protection suspended"
		case 24621:
			event.Expected = "BitLocker enabled"
			event.Actual = "BitLocker protection resumed"
			event.Passed = true // Resume is a good thing
		default:
			event.Expected = "BitLocker protection enabled"
			event.Actual = "BitLocker status changed"
		}

	case channel.Name == "Security":
		event.CheckType = "security_audit"
		event.HIPAAControl = "164.312(b)"
		switch eventID {
		case 4625:
			event.Expected = "Successful authentication"
			event.Actual = "Failed logon attempt"
		case 4740:
			event.Expected = "Account active"
			event.Actual = "Account locked out"
		case 4672:
			event.Expected = "Standard privileges"
			event.Actual = "Special privileges assigned to logon"
			event.Passed = true // Informational, not a failure
		case 4719:
			event.Expected = "Audit policy unchanged"
			event.Actual = "System audit policy changed"
		default:
			event.Expected = "Normal security activity"
			event.Actual = fmt.Sprintf("Security event %d", eventID)
		}

	case channel.Name == "System":
		event.CheckType = "service_status"
		event.HIPAAControl = "164.308(a)(1)"
		switch eventID {
		case 7036:
			event.Expected = "Critical services running"
			event.Actual = "Service entered stopped/running state"
		case 7040:
			event.Expected = "Service startup type unchanged"
			event.Actual = "Service startup type changed"
		default:
			event.Expected = "System services stable"
			event.Actual = fmt.Sprintf("System event %d", eventID)
		}

	default:
		event.CheckType = "unknown"
		event.Expected = "No events"
		event.Actual = fmt.Sprintf("Event %d on %s", eventID, channel.Name)
	}

	return event
}

// Stop stops monitoring event logs
func (w *Watcher) Stop() {
	w.mu.Lock()
	defer w.mu.Unlock()

	if !w.running {
		return
	}

	w.cancel()

	// Close all subscriptions
	for _, handle := range w.subscriptions {
		procEvtClose.Call(handle)
	}
	w.subscriptions = nil
	w.running = false

	log.Println("[EventLog] Stopped Windows Event Log monitoring")
}

// IsRunning returns whether the watcher is running
func (w *Watcher) IsRunning() bool {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.running
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
