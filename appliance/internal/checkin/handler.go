package checkin

import (
	"encoding/json"
	"io"
	"log"
	"net/http"
	"strings"
	"time"
)

// Handler serves the /api/appliances/checkin HTTP endpoint.
type Handler struct {
	db        *DB
	authToken string // If non-empty, validates Bearer token on every request
}

// NewHandler creates a new checkin handler.
// If authToken is non-empty, all requests must include a matching Bearer token.
func NewHandler(db *DB, authToken string) *Handler {
	return &Handler{db: db, authToken: authToken}
}

// ServeHTTP handles POST /api/appliances/checkin.
func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Read body once so we can parse site_id for per-site auth
	body, err := io.ReadAll(r.Body)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"error": "failed to read body",
		})
		return
	}

	// Parse request
	var req CheckinRequest
	if err := json.Unmarshal(body, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"error": "invalid JSON: " + err.Error(),
		})
		return
	}

	// Validate required fields
	if req.SiteID == "" || req.Hostname == "" || req.MACAddress == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"error": "site_id, hostname, and mac_address are required",
		})
		return
	}

	// Validate auth: accept static token OR per-site API key
	auth := r.Header.Get("Authorization")
	bearerToken := strings.TrimPrefix(auth, "Bearer ")
	if !strings.HasPrefix(auth, "Bearer ") {
		bearerToken = ""
	}

	if h.authToken != "" || bearerToken != "" {
		authorized := false

		// Check 1: static auth token match
		if h.authToken != "" && bearerToken == h.authToken {
			authorized = true
		}

		// Check 2: per-site API key from appliance_provisioning
		if !authorized && bearerToken != "" && req.SiteID != "" && h.db != nil {
			valid, err := h.db.ValidateAPIKey(r.Context(), req.SiteID, bearerToken)
			if err != nil {
				log.Printf("[checkin] per-site auth check error for %s: %v", req.SiteID, err)
			}
			if valid {
				authorized = true
			}
		}

		// If auth token is configured but neither method matched, reject
		if h.authToken != "" && !authorized {
			writeJSON(w, http.StatusUnauthorized, map[string]string{
				"error": "invalid or missing Bearer token",
			})
			return
		}
	}

	start := time.Now()

	// Process checkin
	resp, err := h.db.ProcessCheckin(r.Context(), req)
	if err != nil {
		log.Printf("[checkin] ERROR processing %s/%s: %v", req.SiteID, req.Hostname, err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{
			"error": "checkin failed",
		})
		return
	}

	elapsed := time.Since(start)
	log.Printf("[checkin] %s/%s -> %s (%d orders, %d win, %d lin) in %v",
		req.SiteID, req.Hostname, resp.ApplianceID,
		len(resp.PendingOrders), len(resp.WindowsTargets), len(resp.LinuxTargets),
		elapsed)

	writeJSON(w, http.StatusOK, resp)
}

// RegisterRoutes adds the checkin route to a ServeMux.
func RegisterRoutes(mux *http.ServeMux, handler *Handler) {
	mux.Handle("/api/appliances/checkin", handler)
}

func writeJSON(w http.ResponseWriter, status int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}
