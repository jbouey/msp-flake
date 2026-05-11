package l2bridge

import (
	"testing"
)

// ptrBool returns a pointer to b — Go's `*bool` JSON-omitempty pattern
// requires a pointer so the "absent" vs "explicit false" distinction
// survives JSON unmarshaling.
func ptrBool(b bool) *bool { return &b }

// TestShouldExecute_L2DecisionRecorded_FalseBlocks pins the
// Session 219 round-3 defense-in-depth: when the server-side
// /api/agent/l2/plan endpoint returns `l2_decision_recorded=false`,
// the daemon MUST refuse to execute even if every other field looks
// executable.
//
// Mirrors the Python forward fix in agent_api.agent_l2_plan
// (Session 219 mig 300/301/302 + round-3 commit f9537612).
//
// Without this gate, a future server-side regression that drops
// the escalate_to_l3=true safeguard would silently let ghost-L2
// incidents resume.
func TestShouldExecute_L2DecisionRecorded_FalseBlocks(t *testing.T) {
	d := &LLMDecision{
		Confidence:         0.9,    // high — would normally execute
		RequiresApproval:   false,  // would normally execute
		EscalateToL3:       false,  // would normally execute
		RunbookID:          "RB-WIN-PATCH-001",
		L2DecisionRecorded: ptrBool(false), // <-- server signal
	}
	if d.ShouldExecute() {
		t.Fatal("ShouldExecute must return false when " +
			"L2DecisionRecorded is explicitly false — defense-in-depth " +
			"against ghost-L2 incident class")
	}
}

func TestShouldExecute_L2DecisionRecorded_TrueAllows(t *testing.T) {
	d := &LLMDecision{
		Confidence:         0.9,
		RequiresApproval:   false,
		EscalateToL3:       false,
		RunbookID:          "RB-WIN-PATCH-001",
		L2DecisionRecorded: ptrBool(true),
	}
	if !d.ShouldExecute() {
		t.Fatal("ShouldExecute should allow when all gates pass " +
			"and L2DecisionRecorded=true")
	}
}

func TestShouldExecute_L2DecisionRecorded_NilFallsBackToOldGates(t *testing.T) {
	// Older server versions don't send the field — pointer is nil.
	// Daemon must NOT block on that; the existing EscalateToL3 +
	// RequiresApproval + Confidence gates handle the old shape.
	d := &LLMDecision{
		Confidence:         0.9,
		RequiresApproval:   false,
		EscalateToL3:       false,
		RunbookID:          "RB-WIN-PATCH-001",
		L2DecisionRecorded: nil,
	}
	if !d.ShouldExecute() {
		t.Fatal("ShouldExecute must NOT block when L2DecisionRecorded " +
			"is nil (older server) — old gates should still apply")
	}
}

func TestShouldExecute_EscalateToL3_BlocksRegardlessOfRecord(t *testing.T) {
	d := &LLMDecision{
		Confidence:         0.9,
		RequiresApproval:   false,
		EscalateToL3:       true, // <-- old gate still primary
		RunbookID:          "RB-WIN-PATCH-001",
		L2DecisionRecorded: ptrBool(true),
	}
	if d.ShouldExecute() {
		t.Fatal("ShouldExecute must still block on EscalateToL3 even " +
			"when L2DecisionRecorded is true — both gates are AND-ed")
	}
}
