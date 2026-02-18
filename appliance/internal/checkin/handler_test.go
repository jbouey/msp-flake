package checkin

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHandlerMethodNotAllowed(t *testing.T) {
	handler := &Handler{db: nil, authToken: ""}

	req := httptest.NewRequest(http.MethodGet, "/api/appliances/checkin", nil)
	w := httptest.NewRecorder()

	handler.ServeHTTP(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", w.Code)
	}
}

func TestHandlerBadJSON(t *testing.T) {
	handler := &Handler{db: nil, authToken: ""}

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
	handler := &Handler{db: nil, authToken: ""}

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
	handler := &Handler{db: nil, authToken: ""}
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

func TestHandlerAuthRejectsNoToken(t *testing.T) {
	handler := &Handler{db: nil, authToken: "secret-token-123"}

	body, _ := json.Marshal(CheckinRequest{
		SiteID: "site-1", Hostname: "ws01", MACAddress: "aa:bb:cc:dd:ee:ff",
	})

	req := httptest.NewRequest(http.MethodPost, "/api/appliances/checkin", bytes.NewBuffer(body))
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 without token, got %d", w.Code)
	}
}

func TestHandlerAuthRejectsWrongToken(t *testing.T) {
	handler := &Handler{db: nil, authToken: "secret-token-123"}

	body, _ := json.Marshal(CheckinRequest{
		SiteID: "site-1", Hostname: "ws01", MACAddress: "aa:bb:cc:dd:ee:ff",
	})

	req := httptest.NewRequest(http.MethodPost, "/api/appliances/checkin", bytes.NewBuffer(body))
	req.Header.Set("Authorization", "Bearer wrong-token")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 with wrong token, got %d", w.Code)
	}
}

func TestHandlerAuthPassesCorrectToken(t *testing.T) {
	handler := &Handler{db: nil, authToken: "secret-token-123"}

	// Use a request missing required fields so it stops at validation, not at nil db
	body, _ := json.Marshal(CheckinRequest{SiteID: "site-1"})

	req := httptest.NewRequest(http.MethodPost, "/api/appliances/checkin", bytes.NewBuffer(body))
	req.Header.Set("Authorization", "Bearer secret-token-123")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	// Should get 400 (validation error), NOT 401 (auth rejected)
	if w.Code == http.StatusUnauthorized {
		t.Fatal("expected auth to pass with correct token")
	}
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 after auth passes, got %d", w.Code)
	}
}

func TestHandlerNoAuthWhenUnconfigured(t *testing.T) {
	handler := &Handler{db: nil, authToken: ""}

	// Use incomplete request so it stops at validation (400), not at nil db
	body, _ := json.Marshal(CheckinRequest{SiteID: "site-1"})

	// No auth header, no configured token → should pass auth, hit validation
	req := httptest.NewRequest(http.MethodPost, "/api/appliances/checkin", bytes.NewBuffer(body))
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	if w.Code == http.StatusUnauthorized {
		t.Fatal("should not require auth when token is unconfigured")
	}
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
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
