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
	// This returns the Windows agent binary info (legacy path).
	CurrentAgentVersion() (version, sha256hex, downloadURL string, ok bool)

	// AgentVersionForPlatform returns update info for a specific platform/arch/osVersion.
	// Uses the AgentManifest for macOS/Linux, falls back to CurrentAgentVersion for Windows.
	AgentVersionForPlatform(platform, arch, osVersion string) (version, sha256hex, downloadURL string, ok bool)
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

// Invalidate clears the cached manifest, forcing recomputation on the next get() call.
// Call this after writing a new agent binary (e.g. from a fleet order) so the gRPC
// heartbeat picks up the change immediately instead of waiting for modtime detection.
func (c *agentVersionCache) Invalidate() {
	c.mu.Lock()
	c.info = nil
	c.modTime = time.Time{}
	c.mu.Unlock()
	log.Printf("[agent-version] Cache invalidated — next get() will recompute")
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
		if err := json.NewEncoder(w).Encode(info); err != nil {
			log.Printf("[agent-version] Failed to encode response: %v", err)
		}
	}
}

// CurrentAgentVersion implements AgentVersionProvider.
// Called by the gRPC server to populate update fields in HeartbeatResponse.
// Returns the Windows agent binary info (legacy path: /var/lib/msp/agent/).
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

// AgentVersionForPlatform returns update info for a specific platform/arch pair.
// Uses the AgentManifest for multi-platform lookups (macOS, Linux).
// Falls back to CurrentAgentVersion for Windows (legacy path).
// If arch is empty, it is inferred from platform and osVersion.
func (d *Daemon) AgentVersionForPlatform(platform, arch, osVersion string) (version, sha256hex, downloadURL string, ok bool) {
	// Windows: use the legacy agentVersionCache (binary at /var/lib/msp/agent/osiris-agent.exe)
	if platform == "windows" {
		return d.CurrentAgentVersion()
	}

	// Infer arch if not provided (gRPC heartbeat passes "" since the proto has no arch field).
	if arch == "" {
		arch = InferArch(platform, osVersion)
	}

	// macOS/Linux: use the AgentManifest (binaries at /var/lib/msp/bin/)
	if d.agentManifest == nil {
		return "", "", "", false
	}

	entry := d.agentManifest.LookupCompatible(platform, arch, osVersion)
	if entry == nil {
		return "", "", "", false
	}

	if entry.Version == "" || entry.Version == "unknown" {
		return "", "", "", false
	}

	listenAddr := d.config.GRPCListenAddr()
	host := listenAddr
	if idx := strings.LastIndex(host, ":"); idx >= 0 {
		host = host[:idx]
	}
	url := fmt.Sprintf("http://%s:8090/bin/%s", host, entry.Filename)

	return entry.Version, entry.SHA256, url, true
}

// InvalidateAgentVersionCache clears the cached Windows agent version info.
// Called after a fleet order updates the agent binary so the change propagates
// to connected agents immediately via the next heartbeat.
func (d *Daemon) InvalidateAgentVersionCache() {
	if d.agentVersionCache != nil {
		d.agentVersionCache.Invalidate()
	}
}

// RescanAgentManifest re-scans the bin/ directory for multi-platform agent binaries.
// Called after a fleet order drops a new binary so the manifest picks it up immediately.
func (d *Daemon) RescanAgentManifest() {
	if d.agentManifest == nil {
		return
	}
	binDir := filepath.Join(d.config.StateDir, "bin")
	if err := d.agentManifest.ScanDirectory(binDir); err != nil {
		log.Printf("[agent-version] Manifest rescan failed: %v", err)
	} else {
		log.Printf("[agent-version] Manifest rescan complete: %d binaries", d.agentManifest.Count())
	}
}
