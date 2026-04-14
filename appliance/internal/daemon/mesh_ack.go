// Package daemon — M3 Go-side implementation.
// Daemon ACKs its currently-held mesh targets to /api/appliances/mesh/ack
// so the server's mesh_reassignment_loop can reassign unACKed targets to
// live appliances. Paired with Session 206 server-side mesh_targets.py
// + Migration 195.

package daemon

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"time"
)

// MeshTargetAckRequest matches server mesh_targets.MeshTargetAck.
type MeshTargetAckRequest struct {
	SiteID       string               `json:"site_id"`
	ApplianceID  string               `json:"appliance_id"`
	Targets      []MeshTargetAckEntry `json:"targets"`
}

type MeshTargetAckEntry struct {
	TargetKey  string `json:"target_key"`
	TargetType string `json:"target_type"`
}

// MeshTargetAckResponse matches server mesh_targets.MeshTargetsAckResponse.
type MeshTargetAckResponse struct {
	Acked         int `json:"acked"`
	Unknown       int `json:"unknown"`
	Reassigned    int `json:"reassigned"`
	TotalAssigned int `json:"total_assigned"`
}

// PostMeshAck sends an ACK for every target this appliance believes it owns.
// Called after each successful checkin cycle. A non-zero `reassigned` count
// means the server took targets away — the daemon should re-fetch its
// authoritative list via /api/appliances/mesh/assignments.
//
// Failure is non-fatal: mesh reassignment will happen on the server side
// via TTL expiry regardless. This ACK just keeps TTL fresh for targets
// we're confirming we own.
func PostMeshAck(
	ctx context.Context,
	apiEndpoint, apiKey, siteID, applianceID string,
	targets []MeshTargetAckEntry,
	httpClient *http.Client,
) (*MeshTargetAckResponse, error) {
	if len(targets) == 0 {
		return &MeshTargetAckResponse{}, nil
	}
	if httpClient == nil {
		httpClient = &http.Client{Timeout: 15 * time.Second}
	}

	body, err := json.Marshal(MeshTargetAckRequest{
		SiteID:      siteID,
		ApplianceID: applianceID,
		Targets:     targets,
	})
	if err != nil {
		return nil, fmt.Errorf("marshal mesh ack: %w", err)
	}

	url := apiEndpoint + "/api/appliances/mesh/ack"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("build mesh ack request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+apiKey)

	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("mesh ack POST: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		slog.Warn("mesh ack non-200",
			"component", "mesh_ack",
			"status", resp.StatusCode,
			"url", url,
		)
		return nil, fmt.Errorf("mesh ack status %d", resp.StatusCode)
	}

	var out MeshTargetAckResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, fmt.Errorf("decode mesh ack response: %w", err)
	}

	slog.Info("mesh ack ok",
		"component", "mesh_ack",
		"acked", out.Acked,
		"unknown", out.Unknown,
		"reassigned", out.Reassigned,
		"total", out.TotalAssigned,
	)
	return &out, nil
}

// FetchMeshAssignments retrieves the authoritative target list from the
// server — used when the ACK reply shows reassignment activity (our local
// view is out of sync). Corresponds to
// /api/appliances/mesh/assignments.
type MeshAssignment struct {
	TargetKey   string `json:"target_key"`
	TargetType  string `json:"target_type"`
	LastAckAt   string `json:"last_ack_at,omitempty"`
	AckCount    int    `json:"ack_count"`
	ExpiresAt   string `json:"expires_at"`
}

type MeshAssignmentsResponse struct {
	SiteID      string           `json:"site_id"`
	Assignments []MeshAssignment `json:"assignments"`
	GeneratedAt string           `json:"generated_at"`
}

func FetchMeshAssignments(
	ctx context.Context,
	apiEndpoint, apiKey string,
	httpClient *http.Client,
) (*MeshAssignmentsResponse, error) {
	if httpClient == nil {
		httpClient = &http.Client{Timeout: 15 * time.Second}
	}
	url := apiEndpoint + "/api/appliances/mesh/assignments"
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+apiKey)
	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("fetch mesh assignments status %d", resp.StatusCode)
	}
	var out MeshAssignmentsResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, err
	}
	return &out, nil
}
