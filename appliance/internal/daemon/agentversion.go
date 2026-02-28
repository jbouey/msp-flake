package daemon

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// AgentVersionProvider supplies the current agent binary version info.
// Implemented by the Daemon and consumed by the gRPC server.
type AgentVersionProvider interface {
	// CurrentAgentVersion returns the available agent version, SHA256, and download URL.
	// Returns ok=false if no agent binary is available.
	CurrentAgentVersion() (version, sha256hex, downloadURL string, ok bool)
}

// agentVersionCache holds the cached version manifest for the agent binary.
type agentVersionCache struct {
	mu       sync.RWMutex
	info     *AgentVersionInfo
	modTime  time.Time
	agentDir string
}

// AgentVersionInfo is the JSON manifest served at /agent/version.json.
type AgentVersionInfo struct {
	Version   string `json:"version"`
	SHA256    string `json:"sha256"`
	Size      int64  `json:"size"`
	Filename  string `json:"filename"`
	UpdatedAt string `json:"updated_at"`
}

func newAgentVersionCache(agentDir string) *agentVersionCache {
	return &agentVersionCache{agentDir: agentDir}
}

// get returns the cached manifest, recomputing if the binary has changed.
func (c *agentVersionCache) get() (*AgentVersionInfo, error) {
	binPath := filepath.Join(c.agentDir, "osiris-agent.exe")
	stat, err := os.Stat(binPath)
	if err != nil {
		return nil, fmt.Errorf("agent binary not found: %w", err)
	}

	c.mu.RLock()
	if c.info != nil && stat.ModTime().Equal(c.modTime) {
		info := c.info
		c.mu.RUnlock()
		return info, nil
	}
	c.mu.RUnlock()

	// Recompute
	hash, size, err := computeAgentSHA256(binPath)
	if err != nil {
		return nil, fmt.Errorf("compute SHA256: %w", err)
	}

	version := readVersionFile(c.agentDir)

	info := &AgentVersionInfo{
		Version:   version,
		SHA256:    hash,
		Size:      size,
		Filename:  "osiris-agent.exe",
		UpdatedAt: stat.ModTime().UTC().Format(time.RFC3339),
	}

	c.mu.Lock()
	c.info = info
	c.modTime = stat.ModTime()
	c.mu.Unlock()

	log.Printf("[agent-version] Cached manifest: v%s sha256=%s size=%d", version, hash[:12], size)
	return info, nil
}

// computeAgentSHA256 reads the binary and returns its hex-encoded SHA256 and size.
func computeAgentSHA256(path string) (string, int64, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", 0, err
	}
	defer f.Close()

	h := sha256.New()
	n, err := io.Copy(h, f)
	if err != nil {
		return "", 0, err
	}

	return hex.EncodeToString(h.Sum(nil)), n, nil
}

// readVersionFile reads the VERSION sidecar file from the agent directory.
// Falls back to "unknown" if the file doesn't exist.
func readVersionFile(agentDir string) string {
	data, err := os.ReadFile(filepath.Join(agentDir, "VERSION"))
	if err != nil {
		return "unknown"
	}
	return strings.TrimSpace(string(data))
}

// handleAgentVersion returns an HTTP handler that serves the version manifest JSON.
func (d *Daemon) handleAgentVersion(cache *agentVersionCache) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		info, err := cache.get()
		if err != nil {
			http.Error(w, err.Error(), http.StatusServiceUnavailable)
			return
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(info)
	}
}

// CurrentAgentVersion implements AgentVersionProvider.
// Called by the gRPC server to populate update fields in HeartbeatResponse.
func (d *Daemon) CurrentAgentVersion() (version, sha256hex, downloadURL string, ok bool) {
	if d.agentVersionCache == nil {
		return "", "", "", false
	}

	info, err := d.agentVersionCache.get()
	if err != nil {
		return "", "", "", false
	}

	if info.Version == "" || info.Version == "unknown" {
		return "", "", "", false
	}

	// Build download URL from the daemon's LAN address
	listenAddr := d.config.GRPCListenAddr()
	host := listenAddr
	if idx := strings.LastIndex(host, ":"); idx >= 0 {
		host = host[:idx]
	}
	url := fmt.Sprintf("http://%s:8090/agent/%s", host, info.Filename)

	return info.Version, info.SHA256, url, true
}
