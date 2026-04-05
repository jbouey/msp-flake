package l2planner

import (
	"strings"
	"testing"

	"github.com/osiriscare/appliance/internal/l2bridge"
)

func TestValidateDecision_Valid(t *testing.T) {
	d := &l2bridge.LLMDecision{
		RecommendedAction: "restart_service",
		Confidence:        0.85,
		ActionParams:      map[string]interface{}{"service": "sshd"},
	}

	if err := ValidateDecision(d, nil, nil); err != nil {
		t.Errorf("Valid decision should pass: %v", err)
	}
}

func TestValidateDecision_ValidRunbook(t *testing.T) {
	knownRunbooks := map[string]bool{
		"RB-FIREWALL-001": true,
		"RB-SERVICE-001":  true,
	}

	d := &l2bridge.LLMDecision{
		RecommendedAction: "execute_runbook",
		RunbookID:         "RB-FIREWALL-001",
		Confidence:        0.9,
		ActionParams:      map[string]interface{}{},
	}

	if err := ValidateDecision(d, knownRunbooks, nil); err != nil {
		t.Errorf("Valid runbook decision should pass: %v", err)
	}
}

func TestValidateDecision_EmptyAction(t *testing.T) {
	d := &l2bridge.LLMDecision{
		RecommendedAction: "",
		Confidence:        0.85,
	}

	err := ValidateDecision(d, nil, nil)
	if err == nil {
		t.Fatal("Empty action should fail validation")
	}
	if !strings.Contains(err.Error(), "empty recommended_action") {
		t.Errorf("Error should mention empty action: %v", err)
	}
}

func TestValidateDecision_UnknownRunbook(t *testing.T) {
	knownRunbooks := map[string]bool{
		"RB-FIREWALL-001": true,
		"RB-SERVICE-001":  true,
	}

	d := &l2bridge.LLMDecision{
		RecommendedAction: "execute_runbook",
		RunbookID:         "RB-NONEXISTENT-999",
		Confidence:        0.9,
		ActionParams:      map[string]interface{}{},
	}

	err := ValidateDecision(d, knownRunbooks, nil)
	if err == nil {
		t.Fatal("Unknown runbook should fail validation")
	}
	if !strings.Contains(err.Error(), "unknown runbook_id") {
		t.Errorf("Error should mention unknown runbook: %v", err)
	}
}

func TestValidateDecision_ExecuteRunbookWithoutID(t *testing.T) {
	d := &l2bridge.LLMDecision{
		RecommendedAction: "execute_runbook",
		RunbookID:         "",
		Confidence:        0.85,
		ActionParams:      map[string]interface{}{},
	}

	err := ValidateDecision(d, nil, nil)
	if err == nil {
		t.Fatal("execute_runbook without runbook_id should fail")
	}
	if !strings.Contains(err.Error(), "without runbook_id") {
		t.Errorf("Error should mention missing runbook_id: %v", err)
	}
}

func TestValidateDecision_RunPrefixWithoutID(t *testing.T) {
	d := &l2bridge.LLMDecision{
		RecommendedAction: "run_remediation",
		RunbookID:         "",
		Confidence:        0.8,
		ActionParams:      map[string]interface{}{},
	}

	err := ValidateDecision(d, nil, nil)
	if err == nil {
		t.Fatal("run_* without runbook_id should fail")
	}
	if !strings.Contains(err.Error(), "without runbook_id") {
		t.Errorf("Error should mention missing runbook_id: %v", err)
	}
}

func TestValidateDecision_ConfidenceOutOfRange(t *testing.T) {
	tests := []struct {
		name       string
		confidence float64
	}{
		{"negative", -0.1},
		{"above_one", 1.5},
		{"very_negative", -100.0},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			d := &l2bridge.LLMDecision{
				RecommendedAction: "restart_service",
				Confidence:        tt.confidence,
				ActionParams:      map[string]interface{}{},
			}

			err := ValidateDecision(d, nil, nil)
			if err == nil {
				t.Fatalf("Confidence %f should fail validation", tt.confidence)
			}
			if !strings.Contains(err.Error(), "confidence out of range") {
				t.Errorf("Error should mention confidence: %v", err)
			}
		})
	}
}

func TestValidateDecision_ConfidenceBoundaries(t *testing.T) {
	// Exactly 0 and 1 should be valid
	for _, conf := range []float64{0.0, 1.0} {
		d := &l2bridge.LLMDecision{
			RecommendedAction: "restart_service",
			Confidence:        conf,
			ActionParams:      map[string]interface{}{},
		}
		if err := ValidateDecision(d, nil, nil); err != nil {
			t.Errorf("Confidence %f should be valid: %v", conf, err)
		}
	}
}

func TestValidateDecision_UnknownHost(t *testing.T) {
	knownHosts := []string{"DC01", "WS01", "APP01"}

	d := &l2bridge.LLMDecision{
		RecommendedAction: "restart_service",
		Confidence:        0.85,
		ActionParams: map[string]interface{}{
			"host_id": "ROGUE-HOST",
		},
	}

	err := ValidateDecision(d, nil, knownHosts)
	if err == nil {
		t.Fatal("Unknown host should fail validation")
	}
	if !strings.Contains(err.Error(), "unknown target host") {
		t.Errorf("Error should mention unknown host: %v", err)
	}
}

func TestValidateDecision_KnownHostCaseInsensitive(t *testing.T) {
	knownHosts := []string{"DC01", "WS01"}

	d := &l2bridge.LLMDecision{
		RecommendedAction: "restart_service",
		Confidence:        0.85,
		ActionParams: map[string]interface{}{
			"host_id": "dc01", // lowercase
		},
	}

	if err := ValidateDecision(d, nil, knownHosts); err != nil {
		t.Errorf("Host match should be case-insensitive: %v", err)
	}
}

func TestValidateDecision_NoHostListSkipsCheck(t *testing.T) {
	d := &l2bridge.LLMDecision{
		RecommendedAction: "restart_service",
		Confidence:        0.85,
		ActionParams: map[string]interface{}{
			"host_id": "ANY-HOST",
		},
	}

	// nil knownHosts = skip host validation
	if err := ValidateDecision(d, nil, nil); err != nil {
		t.Errorf("No host list should skip host check: %v", err)
	}

	// empty knownHosts = skip host validation
	if err := ValidateDecision(d, nil, []string{}); err != nil {
		t.Errorf("Empty host list should skip host check: %v", err)
	}
}

func TestValidateDecision_NilRunbooksSkipsCheck(t *testing.T) {
	d := &l2bridge.LLMDecision{
		RecommendedAction: "execute_runbook",
		RunbookID:         "RB-ANYTHING",
		Confidence:        0.9,
		ActionParams:      map[string]interface{}{},
	}

	// nil knownRunbooks = only check that runbook_id is non-empty
	if err := ValidateDecision(d, nil, nil); err != nil {
		t.Errorf("Nil runbook registry should skip lookup: %v", err)
	}
}

func TestValidateDecision_NilActionParams(t *testing.T) {
	d := &l2bridge.LLMDecision{
		RecommendedAction: "escalate",
		Confidence:        0.5,
		ActionParams:      nil,
	}

	if err := ValidateDecision(d, nil, nil); err != nil {
		t.Errorf("Nil action params should not panic: %v", err)
	}
}
