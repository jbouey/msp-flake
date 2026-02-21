package evidence

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestBuildAndSubmit_NoHosts(t *testing.T) {
	s := NewSubmitter("site-1", "http://localhost", "key", nil, "")
	err := s.BuildAndSubmit(context.Background(), nil, nil)
	if err != nil {
		t.Fatalf("expected nil for empty hosts, got: %v", err)
	}
}

func TestBuildAndSubmit_AllPass(t *testing.T) {
	dir := t.TempDir()
	priv, pubHex, err := LoadOrCreateSigningKey(dir + "/signing.key")
	if err != nil {
		t.Fatal(err)
	}

	var receivedPayload bundlePayload

	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		json.Unmarshal(body, &receivedPayload)

		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"bundle_id":"CB-test","chain_position":1,"prev_hash":null,"current_hash":"abc123"}`))
	}))
	defer ts.Close()

	s := NewSubmitter("site-1", ts.URL, "test-key", priv, pubHex)

	// No findings = all pass
	err = s.BuildAndSubmit(context.Background(), nil, []string{"dc01", "ws01"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// Should have 7 check types x 2 hosts = 14 checks, all pass
	if len(receivedPayload.Checks) != 14 {
		t.Fatalf("expected 14 checks, got %d", len(receivedPayload.Checks))
	}

	summary := receivedPayload.Summary
	compliant, _ := summary["compliant"].(float64)
	nonCompliant, _ := summary["non_compliant"].(float64)

	if int(compliant) != 14 {
		t.Fatalf("expected 14 compliant, got %v", compliant)
	}
	if int(nonCompliant) != 0 {
		t.Fatalf("expected 0 non_compliant, got %v", nonCompliant)
	}

	// Verify signature and public key were sent
	if receivedPayload.AgentPublicKey != pubHex {
		t.Fatalf("public key mismatch")
	}
	if receivedPayload.AgentSignature == "" {
		t.Fatal("signature not sent")
	}
	if receivedPayload.SignedData == "" {
		t.Fatal("signed_data not sent")
	}
}

func TestBuildAndSubmit_WithDrift(t *testing.T) {
	dir := t.TempDir()
	priv, pubHex, err := LoadOrCreateSigningKey(dir + "/signing.key")
	if err != nil {
		t.Fatal(err)
	}

	var receivedPayload bundlePayload

	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		json.Unmarshal(body, &receivedPayload)
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"bundle_id":"CB-test","chain_position":2,"prev_hash":"abc","current_hash":"def"}`))
	}))
	defer ts.Close()

	s := NewSubmitter("site-1", ts.URL, "test-key", priv, pubHex)

	findings := []DriftFinding{
		{Hostname: "dc01", CheckType: "firewall_status", Expected: "True", Actual: "False", HIPAAControl: "164.312(a)(1)"},
		{Hostname: "dc01", CheckType: "windows_defender", Expected: "Running", Actual: "Stopped", HIPAAControl: "164.308(a)(5)"},
	}

	err = s.BuildAndSubmit(context.Background(), findings, []string{"dc01"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// 7 check types x 1 host = 7 checks total
	if len(receivedPayload.Checks) != 7 {
		t.Fatalf("expected 7 checks, got %d", len(receivedPayload.Checks))
	}

	summary := receivedPayload.Summary
	compliant, _ := summary["compliant"].(float64)
	nonCompliant, _ := summary["non_compliant"].(float64)

	// 2 drifts found, 5 pass
	if int(compliant) != 5 {
		t.Fatalf("expected 5 compliant, got %v", compliant)
	}
	if int(nonCompliant) != 2 {
		t.Fatalf("expected 2 non_compliant, got %v", nonCompliant)
	}

	// Verify the failed checks have the right details
	failCount := 0
	for _, check := range receivedPayload.Checks {
		if status, _ := check["status"].(string); status == "fail" {
			failCount++
		}
	}
	if failCount != 2 {
		t.Fatalf("expected 2 failed checks, got %d", failCount)
	}
}

func TestBuildAndSubmit_ServerError(t *testing.T) {
	dir := t.TempDir()
	priv, pubHex, _ := LoadOrCreateSigningKey(dir + "/signing.key")

	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(500)
		w.Write([]byte(`{"detail":"server error"}`))
	}))
	defer ts.Close()

	s := NewSubmitter("site-1", ts.URL, "test-key", priv, pubHex)
	err := s.BuildAndSubmit(context.Background(), nil, []string{"dc01"})
	if err == nil {
		t.Fatal("expected error on 500 response")
	}
}
