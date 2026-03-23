package daemon

import (
	"testing"
	"time"
)

func TestRogueDetector_BaselineSuppression(t *testing.T) {
	rd := newRogueDetector() // baseline is 24h from now
	devices := []discoveredDevice{{MACAddress: "aa:bb:cc:dd:ee:ff", IPAddress: "192.168.1.1"}}
	rogues := rd.checkForRogues(devices)
	if len(rogues) != 0 {
		t.Error("should suppress during baseline period")
	}
	if _, ok := rd.knownMACs["aa:bb:cc:dd:ee:ff"]; !ok {
		t.Error("should still learn MACs during baseline")
	}
}

func TestRogueDetector_NewDevice(t *testing.T) {
	rd := &rogueDetector{
		knownMACs:     map[string]time.Time{"aa:bb:cc:dd:ee:ff": time.Now()},
		baselineUntil: time.Now().Add(-1 * time.Hour), // baseline expired
		alertWindow:   time.Now(),
	}
	devices := []discoveredDevice{
		{MACAddress: "aa:bb:cc:dd:ee:ff", IPAddress: "192.168.1.1"}, // known
		{MACAddress: "11:22:33:44:55:66", IPAddress: "192.168.1.2"}, // new = rogue
	}
	rogues := rd.checkForRogues(devices)
	if len(rogues) != 1 || rogues[0].MACAddress != "11:22:33:44:55:66" {
		t.Errorf("expected 1 rogue (11:22:33:44:55:66), got %d", len(rogues))
	}
}

func TestRogueDetector_RateLimit(t *testing.T) {
	rd := &rogueDetector{
		knownMACs:     make(map[string]time.Time),
		baselineUntil: time.Now().Add(-1 * time.Hour),
		alertCount:    10, // at limit
		alertWindow:   time.Now(),
	}
	devices := []discoveredDevice{{MACAddress: "aa:bb:cc:dd:ee:ff", IPAddress: "192.168.1.1"}}
	rogues := rd.checkForRogues(devices)
	if len(rogues) != 0 {
		t.Error("should suppress when at rate limit")
	}
	// But MAC should still be learned
	if _, ok := rd.knownMACs["aa:bb:cc:dd:ee:ff"]; !ok {
		t.Error("should still learn MAC even when rate limited")
	}
}

func TestRogueDetector_EmptyMAC(t *testing.T) {
	rd := &rogueDetector{
		knownMACs:     make(map[string]time.Time),
		baselineUntil: time.Now().Add(-1 * time.Hour),
		alertWindow:   time.Now(),
	}
	devices := []discoveredDevice{{MACAddress: "", IPAddress: "192.168.1.1"}}
	rogues := rd.checkForRogues(devices)
	if len(rogues) != 0 {
		t.Error("should skip devices with empty MAC")
	}
}
