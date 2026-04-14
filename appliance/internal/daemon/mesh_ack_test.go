// Tests for M3 mesh_ack HTTP helpers.
// Enterprise-grade: validate request/response contract, auth header,
// error handling under HTTP failure modes. No network.

package daemon

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestPostMeshAck_SendsExpectedPayload(t *testing.T) {
	var gotBody []byte
	var gotAuth string
	var gotCT string

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Errorf("expected POST, got %s", r.Method)
		}
		if r.URL.Path != "/api/appliances/mesh/ack" {
			t.Errorf("expected /api/appliances/mesh/ack, got %s", r.URL.Path)
		}
		gotAuth = r.Header.Get("Authorization")
		gotCT = r.Header.Get("Content-Type")
		body, _ := io.ReadAll(r.Body)
		gotBody = body
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"acked":2,"unknown":1,"reassigned":0,"total_assigned":3}`))
	}))
	defer srv.Close()

	targets := []MeshTargetAckEntry{
		{TargetKey: "dev-1", TargetType: "device"},
		{TargetKey: "192.168.88.0/24", TargetType: "subnet"},
	}

	resp, err := PostMeshAck(
		context.Background(),
		srv.URL,
		"test-api-key",
		"site-abc",
		"site-abc-aa:bb:cc:dd:ee:ff",
		targets,
		srv.Client(),
	)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// Auth header must be Bearer-token
	if gotAuth != "Bearer test-api-key" {
		t.Errorf("expected Bearer test-api-key, got %q", gotAuth)
	}
	if gotCT != "application/json" {
		t.Errorf("expected application/json, got %q", gotCT)
	}

	// Body must decode to our expected shape
	var decoded MeshTargetAckRequest
	if err := json.Unmarshal(gotBody, &decoded); err != nil {
		t.Fatalf("body not JSON: %v — body=%s", err, gotBody)
	}
	if decoded.SiteID != "site-abc" {
		t.Errorf("site_id: got %q", decoded.SiteID)
	}
	if decoded.ApplianceID != "site-abc-aa:bb:cc:dd:ee:ff" {
		t.Errorf("appliance_id: got %q", decoded.ApplianceID)
	}
	if len(decoded.Targets) != 2 {
		t.Errorf("expected 2 targets, got %d", len(decoded.Targets))
	}

	// Response decoded correctly
	if resp.Acked != 2 || resp.Unknown != 1 || resp.Reassigned != 0 || resp.TotalAssigned != 3 {
		t.Errorf("unexpected response: %+v", resp)
	}
}

func TestPostMeshAck_EmptyTargetsSkipsHTTP(t *testing.T) {
	// Guard: no-op when appliance has no targets — don't waste a call.
	hit := false
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hit = true
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	resp, err := PostMeshAck(
		context.Background(),
		srv.URL,
		"k",
		"s",
		"a",
		nil,
		srv.Client(),
	)
	if err != nil {
		t.Fatalf("unexpected: %v", err)
	}
	if hit {
		t.Error("should not have made HTTP call for empty target list")
	}
	if resp.Acked != 0 || resp.TotalAssigned != 0 {
		t.Errorf("expected zero counts, got %+v", resp)
	}
}

func TestPostMeshAck_Non200ReturnsError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()

	_, err := PostMeshAck(
		context.Background(),
		srv.URL,
		"k",
		"s",
		"a",
		[]MeshTargetAckEntry{{TargetKey: "x", TargetType: "y"}},
		srv.Client(),
	)
	if err == nil {
		t.Error("expected error on 500 response")
	}
	if !strings.Contains(err.Error(), "500") {
		t.Errorf("expected 500 in error, got: %v", err)
	}
}

func TestFetchMeshAssignments_DecodesResponse(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("expected GET, got %s", r.Method)
		}
		if r.URL.Path != "/api/appliances/mesh/assignments" {
			t.Errorf("path: %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{
			"site_id":"site-abc",
			"assignments":[
				{"target_key":"t1","target_type":"device","ack_count":3,"expires_at":"2026-04-14T20:00:00Z"}
			],
			"generated_at":"2026-04-14T19:00:00Z"
		}`))
	}))
	defer srv.Close()

	resp, err := FetchMeshAssignments(context.Background(), srv.URL, "k", srv.Client())
	if err != nil {
		t.Fatalf("unexpected: %v", err)
	}
	if resp.SiteID != "site-abc" {
		t.Errorf("site_id: %q", resp.SiteID)
	}
	if len(resp.Assignments) != 1 {
		t.Fatalf("expected 1 assignment, got %d", len(resp.Assignments))
	}
	if resp.Assignments[0].TargetKey != "t1" || resp.Assignments[0].AckCount != 3 {
		t.Errorf("assignment: %+v", resp.Assignments[0])
	}
}

func TestPostMeshAck_TimeoutPropagates(t *testing.T) {
	// Slow server — client times out.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(2 * time.Second)
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	client := &http.Client{Timeout: 100 * time.Millisecond}
	_, err := PostMeshAck(
		context.Background(),
		srv.URL,
		"k",
		"s",
		"a",
		[]MeshTargetAckEntry{{TargetKey: "x", TargetType: "y"}},
		client,
	)
	if err == nil {
		t.Error("expected timeout error")
	}
}
