package checkin

import (
	"encoding/json"
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

	// Validate Bearer token if configured
	if h.authToken != "" {
		auth := r.Header.Get("Authorization")
		if !strings.HasPrefix(auth, "Bearer ") || strings.TrimPrefix(auth, "Bearer ") != h.authToken {
			writeJSON(w, http.StatusUnauthorized, map[string]string{
				"error": "invalid or missing Bearer token",
			})
			return
		}
	}

	// Parse request
	var req CheckinRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
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
