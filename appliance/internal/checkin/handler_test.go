package checkin

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHandlerMethodNotAllowed(t *testing.T) {
	handler := &Handler{db: nil}

	req := httptest.NewRequest(http.MethodGet, "/api/appliances/checkin", nil)
	w := httptest.NewRecorder()

	handler.ServeHTTP(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", w.Code)
	}
}

func TestHandlerBadJSON(t *testing.T) {
	handler := &Handler{db: nil}

	req := httptest.NewRequest(http.MethodPost, "/api/appliances/checkin",
		bytes.NewBufferString("not json"))
	w := httptest.NewRecorder()

	handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}

	var resp map[string]string
	json.NewDecoder(w.Body).Decode(&resp)
	if resp["error"] == "" {
		t.Fatal("expected error message in response")
	}
}

func TestHandlerMissingRequiredFields(t *testing.T) {
	handler := &Handler{db: nil}

	tests := []struct {
		name string
		body CheckinRequest
	}{
		{"missing site_id", CheckinRequest{Hostname: "ws01", MACAddress: "aa:bb:cc:dd:ee:ff"}},
		{"missing hostname", CheckinRequest{SiteID: "site-1", MACAddress: "aa:bb:cc:dd:ee:ff"}},
		{"missing mac", CheckinRequest{SiteID: "site-1", Hostname: "ws01"}},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			body, _ := json.Marshal(tt.body)
			req := httptest.NewRequest(http.MethodPost, "/api/appliances/checkin",
				bytes.NewBuffer(body))
			w := httptest.NewRecorder()

			handler.ServeHTTP(w, req)

			if w.Code != http.StatusBadRequest {
				t.Fatalf("expected 400, got %d", w.Code)
			}
		})
	}
}

func TestRegisterRoutes(t *testing.T) {
	mux := http.NewServeMux()
	handler := &Handler{db: nil}
	RegisterRoutes(mux, handler)

	// Verify route exists by sending request
	req := httptest.NewRequest(http.MethodGet, "/api/appliances/checkin", nil)
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, req)

	// Should get 405 (method not allowed) not 404
	if w.Code == http.StatusNotFound {
		t.Fatal("route not registered — got 404")
	}
}

func TestWriteJSON(t *testing.T) {
	w := httptest.NewRecorder()
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
	if ct := w.Header().Get("Content-Type"); ct != "application/json" {
		t.Fatalf("expected application/json, got %s", ct)
	}

	var resp map[string]string
	json.NewDecoder(w.Body).Decode(&resp)
	if resp["status"] != "ok" {
		t.Fatalf("expected status=ok, got %s", resp["status"])
	}
}
