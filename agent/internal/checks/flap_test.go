package checks

import (
	"testing"
)

func TestFlapDetector_NormalFailure(t *testing.T) {
	fd := NewFlapDetector()

	// First few failures should always be sent
	if !fd.ShouldSend("firewall", false) {
		t.Error("first failure should be sent")
	}
	if !fd.ShouldSend("firewall", false) {
		t.Error("second failure should be sent (not enough history)")
	}
}

func TestFlapDetector_DetectsFlapping(t *testing.T) {
	fd := NewFlapDetector()

	// Simulate flapping: pass, fail, pass, fail, pass, fail
	fd.ShouldSend("firewall", true)  // pass
	fd.ShouldSend("firewall", false) // fail - sent (not enough history)
	fd.ShouldSend("firewall", true)  // pass
	fd.ShouldSend("firewall", false) // fail - sent (building history)
	fd.ShouldSend("firewall", true)  // pass
	// By now: T, F, T, F, T = 4 transitions in 5 checks

	// Next failure should be suppressed (flapping detected)
	result := fd.ShouldSend("firewall", false)
	if result {
		t.Error("failure during flapping should be suppressed")
	}

	status := fd.Status("firewall")
	if status == "stable" {
		t.Error("expected flapping status, got stable")
	}
}

func TestFlapDetector_StableFailures(t *testing.T) {
	fd := NewFlapDetector()

	// Consistent failures should NOT trigger flap detection
	for i := 0; i < 6; i++ {
		result := fd.ShouldSend("bitlocker", false)
		if !result {
			t.Errorf("consistent failure %d should be sent", i)
		}
	}

	if fd.Status("bitlocker") != "stable" {
		t.Error("consistent failures should be stable, not flapping")
	}
}

func TestFlapDetector_Stabilization(t *testing.T) {
	fd := NewFlapDetector()

	// Trigger flapping
	fd.ShouldSend("fw", true)
	fd.ShouldSend("fw", false)
	fd.ShouldSend("fw", true)
	fd.ShouldSend("fw", false)
	fd.ShouldSend("fw", true)
	fd.ShouldSend("fw", false) // should be suppressed

	// Now stabilize with 3 consecutive passes
	fd.ShouldSend("fw", true)
	fd.ShouldSend("fw", true)
	fd.ShouldSend("fw", true)

	// Should be stable again
	if fd.Status("fw") != "stable" {
		t.Errorf("expected stable after 3 consecutive passes, got %s", fd.Status("fw"))
	}
}

func TestFlapDetector_IndependentChecks(t *testing.T) {
	fd := NewFlapDetector()

	// Flapping on firewall should not affect bitlocker
	fd.ShouldSend("firewall", true)
	fd.ShouldSend("firewall", false)
	fd.ShouldSend("firewall", true)
	fd.ShouldSend("firewall", false)
	fd.ShouldSend("firewall", true)

	// Bitlocker is independent
	if !fd.ShouldSend("bitlocker", false) {
		t.Error("bitlocker failure should be sent independently")
	}
}

func TestFlapDetector_PassDoesNotSend(t *testing.T) {
	fd := NewFlapDetector()

	// Passing checks should never trigger a send
	if fd.ShouldSend("test", true) {
		t.Error("passing check should not trigger send")
	}
}
