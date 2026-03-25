// agent_manifest.go — Binary manifest for multi-platform agent deployment.
//
// AgentManifest tracks available agent binaries with platform/arch metadata.
// Loaded from a JSON file on disk, updated by fleet orders or directory scans.
// Thread-safe via RWMutex. Standalone component — no Daemon dependency.
package daemon

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"
)

// AgentBinary describes a single agent binary with platform and version metadata.
type AgentBinary struct {
	Platform     string `json:"platform"`       // "windows", "linux", "darwin"
	Arch         string `json:"arch"`           // "amd64", "arm64"
	Version      string `json:"version"`        // "0.4.1"
	Filename     string `json:"filename"`       // "osiris-agent-darwin-amd64"
	SHA256       string `json:"sha256"`         // hex-encoded hash
	Size         int64  `json:"size"`           // bytes
	MinOSVersion string `json:"min_os_version"` // "12.0" for macOS, "" for no constraint
	GoVersion    string `json:"go_version"`     // "1.22.12" — which Go built this
	UpdatedAt    string `json:"updated_at"`     // ISO 8601 timestamp
}

// manifestKey returns the lookup key for a platform+arch pair.
func manifestKey(platform, arch string) string {
	return platform + "-" + arch
}

// Key returns the manifest lookup key for this binary.
func (b *AgentBinary) Key() string {
	return manifestKey(b.Platform, b.Arch)
}

// AgentManifest tracks available agent binaries with platform/arch metadata.
// Loaded from a JSON file on disk, updated by fleet orders or directory scans.
type AgentManifest struct {
	mu      sync.RWMutex
	entries map[string]*AgentBinary // key: "platform-arch" e.g. "darwin-amd64"
	path    string                  // manifest.json path on disk
}

// manifestJSON is the serializable form of the manifest.
type manifestJSON struct {
	Entries   []*AgentBinary `json:"entries"`
	UpdatedAt string         `json:"updated_at"`
}

// NewAgentManifest creates a manifest rooted at stateDir.
// If manifest.json exists on disk, it is loaded. Errors during load are logged
// but not fatal — the manifest starts empty and can be populated by ScanDirectory.
func NewAgentManifest(stateDir string) *AgentManifest {
	m := &AgentManifest{
		entries: make(map[string]*AgentBinary),
		path:    filepath.Join(stateDir, "bin", "manifest.json"),
	}
	if err := m.Load(); err != nil {
		// Not fatal: manifest file may not exist yet on first boot.
		if !os.IsNotExist(err) {
			log.Printf("[agent-manifest] Load failed (starting empty): %v", err)
		}
	}
	return m
}

// Lookup finds the best binary for a given platform and arch.
// Returns nil if no matching binary is registered.
func (m *AgentManifest) Lookup(platform, arch string) *AgentBinary {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.entries[manifestKey(platform, arch)]
}

// LookupCompatible finds a compatible binary considering OS version constraints.
// If osVersion is empty, it behaves like Lookup.
// Returns nil if no compatible binary is found.
func (m *AgentManifest) LookupCompatible(platform, arch, osVersion string) *AgentBinary {
	m.mu.RLock()
	defer m.mu.RUnlock()

	entry := m.entries[manifestKey(platform, arch)]
	if entry == nil {
		return nil
	}

	// No OS constraint on the binary — always compatible.
	if entry.MinOSVersion == "" {
		return entry
	}

	// No OS version reported by the host — can't verify, allow it (best effort).
	if osVersion == "" {
		return entry
	}

	// Compare version strings numerically.
	if !versionAtLeast(osVersion, entry.MinOSVersion) {
		return nil
	}

	return entry
}

// Register adds or updates a binary entry and persists to disk.
func (m *AgentManifest) Register(binary AgentBinary) error {
	if binary.Platform == "" || binary.Arch == "" {
		return fmt.Errorf("agent manifest: platform and arch are required")
	}
	if binary.Filename == "" {
		return fmt.Errorf("agent manifest: filename is required")
	}

	if binary.UpdatedAt == "" {
		binary.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
	}

	m.mu.Lock()
	m.entries[binary.Key()] = &binary
	m.mu.Unlock()

	return m.Save()
}

// Entries returns a snapshot of all registered binaries.
func (m *AgentManifest) Entries() []*AgentBinary {
	m.mu.RLock()
	defer m.mu.RUnlock()
	out := make([]*AgentBinary, 0, len(m.entries))
	for _, e := range m.entries {
		cp := *e
		out = append(out, &cp)
	}
	return out
}

// ScanDirectory scans dir for agent binaries using the naming convention:
//
//	osiris-agent-{platform}-{arch}  (linux, darwin)
//	osiris-agent.exe                (windows, assumed amd64)
//
// Detected binaries have their SHA256 computed and are registered in the manifest.
// Existing entries are updated only if the file has changed (different SHA256).
// This method is idempotent — safe to call on every daemon startup.
func (m *AgentManifest) ScanDirectory(dir string) error {
	entries, err := os.ReadDir(dir)
	if err != nil {
		if os.IsNotExist(err) {
			// bin/ doesn't exist yet — not an error, just nothing to scan.
			return nil
		}
		return fmt.Errorf("scan agent dir %s: %w", dir, err)
	}

	scanned := 0
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		name := entry.Name()

		platform, arch := parseBinaryFilename(name)
		if platform == "" {
			continue // not a recognized agent binary
		}

		fullPath := filepath.Join(dir, name)
		info, err := entry.Info()
		if err != nil {
			log.Printf("[agent-manifest] Stat %s: %v", name, err)
			continue
		}

		// Check if we already have this file with the same size — skip SHA256 if unchanged.
		key := manifestKey(platform, arch)
		m.mu.RLock()
		existing := m.entries[key]
		m.mu.RUnlock()

		if existing != nil && existing.Size == info.Size() && existing.Filename == name {
			// Size and filename match — assume unchanged (avoid re-hashing large binaries).
			scanned++
			continue
		}

		hash, size, err := computeSHA256(fullPath)
		if err != nil {
			log.Printf("[agent-manifest] Hash %s: %v", name, err)
			continue
		}

		// If SHA256 matches, no update needed.
		if existing != nil && existing.SHA256 == hash {
			scanned++
			continue
		}

		version := readVersionSidecar(dir)

		ab := AgentBinary{
			Platform:  platform,
			Arch:      arch,
			Version:   version,
			Filename:  name,
			SHA256:    hash,
			Size:      size,
			UpdatedAt: info.ModTime().UTC().Format(time.RFC3339),
		}

		// Set minimum macOS version for Go 1.22+ binaries (requires macOS 11+).
		if platform == "darwin" {
			ab.MinOSVersion = "12.0"
		}

		m.mu.Lock()
		m.entries[key] = &ab
		m.mu.Unlock()

		log.Printf("[agent-manifest] Registered %s/%s: %s (sha256=%s, %d bytes)",
			platform, arch, name, hash[:12], size)
		scanned++
	}

	if scanned > 0 {
		if err := m.Save(); err != nil {
			return fmt.Errorf("save manifest after scan: %w", err)
		}
	}

	return nil
}

// Save persists the manifest to disk as JSON. Creates parent directories if needed.
func (m *AgentManifest) Save() error {
	m.mu.RLock()
	mj := manifestJSON{
		Entries:   make([]*AgentBinary, 0, len(m.entries)),
		UpdatedAt: time.Now().UTC().Format(time.RFC3339),
	}
	for _, e := range m.entries {
		cp := *e
		mj.Entries = append(mj.Entries, &cp)
	}
	m.mu.RUnlock()

	data, err := json.MarshalIndent(mj, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal manifest: %w", err)
	}

	dir := filepath.Dir(m.path)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return fmt.Errorf("create manifest dir: %w", err)
	}

	// Atomic write: write to temp file, then rename.
	tmp := m.path + ".tmp"
	if err := os.WriteFile(tmp, data, 0644); err != nil {
		return fmt.Errorf("write manifest tmp: %w", err)
	}
	if err := os.Rename(tmp, m.path); err != nil {
		os.Remove(tmp)
		return fmt.Errorf("rename manifest: %w", err)
	}

	return nil
}

// Load reads the manifest from disk. Returns os.ErrNotExist if the file doesn't exist.
func (m *AgentManifest) Load() error {
	data, err := os.ReadFile(m.path)
	if err != nil {
		return err
	}

	var mj manifestJSON
	if err := json.Unmarshal(data, &mj); err != nil {
		return fmt.Errorf("parse manifest %s: %w", m.path, err)
	}

	m.mu.Lock()
	defer m.mu.Unlock()

	m.entries = make(map[string]*AgentBinary, len(mj.Entries))
	for _, e := range mj.Entries {
		if e.Platform == "" || e.Arch == "" {
			continue // skip corrupt entries
		}
		m.entries[e.Key()] = e
	}

	return nil
}

// Count returns the number of registered binaries.
func (m *AgentManifest) Count() int {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return len(m.entries)
}

// ---------- filename parsing ----------

// binaryPattern matches "osiris-agent-{platform}-{arch}" filenames.
var binaryPattern = regexp.MustCompile(`^osiris-agent-([a-z]+)-([a-z0-9]+)$`)

// parseBinaryFilename extracts platform and arch from a binary filename.
// Returns empty strings if the filename doesn't match the convention.
func parseBinaryFilename(name string) (platform, arch string) {
	// Windows: osiris-agent.exe → windows/amd64
	if name == "osiris-agent.exe" {
		return "windows", "amd64"
	}

	// Unix: osiris-agent-{platform}-{arch}
	matches := binaryPattern.FindStringSubmatch(name)
	if matches == nil {
		return "", ""
	}

	platform = matches[1]
	arch = matches[2]

	// Validate known platforms.
	switch platform {
	case "linux", "darwin":
		// ok
	default:
		return "", "" // unknown platform
	}

	// Validate known architectures.
	switch arch {
	case "amd64", "arm64":
		// ok
	default:
		return "", ""
	}

	return platform, arch
}

// ---------- version helpers ----------

// readVersionSidecar reads the VERSION sidecar file from the given directory.
// Falls back to "unknown" if the file doesn't exist.
func readVersionSidecar(dir string) string {
	data, err := os.ReadFile(filepath.Join(dir, "VERSION"))
	if err != nil {
		return "unknown"
	}
	return strings.TrimSpace(string(data))
}

// ---------- SHA256 ----------

// computeSHA256 returns the hex-encoded SHA256 and size of a file.
func computeSHA256(path string) (string, int64, error) {
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

// ---------- version comparison ----------

// versionAtLeast returns true if hostVer >= minVer using numeric dot-separated comparison.
// Examples:
//
//	versionAtLeast("14.2.1", "12.0") → true
//	versionAtLeast("11.0",   "12.0") → false
//	versionAtLeast("12.0",   "12.0") → true
func versionAtLeast(hostVer, minVer string) bool {
	hostParts := splitVersionParts(hostVer)
	minParts := splitVersionParts(minVer)

	// Compare each numeric component.
	maxLen := len(hostParts)
	if len(minParts) > maxLen {
		maxLen = len(minParts)
	}

	for i := 0; i < maxLen; i++ {
		h := 0
		if i < len(hostParts) {
			h = hostParts[i]
		}
		m := 0
		if i < len(minParts) {
			m = minParts[i]
		}
		if h < m {
			return false
		}
		if h > m {
			return true
		}
	}

	return true // equal
}

// splitVersionParts parses "14.2.1" → [14, 2, 1].
// Non-numeric segments are treated as 0.
func splitVersionParts(v string) []int {
	// Strip leading non-numeric text (e.g. "macOS " prefix).
	v = strings.TrimSpace(v)
	for i, c := range v {
		if c >= '0' && c <= '9' {
			v = v[i:]
			break
		}
	}

	parts := strings.Split(v, ".")
	result := make([]int, 0, len(parts))
	for _, p := range parts {
		n := 0
		for _, c := range p {
			if c >= '0' && c <= '9' {
				n = n*10 + int(c-'0')
			} else {
				break // stop at first non-digit
			}
		}
		result = append(result, n)
	}
	return result
}

// ---------- platform detection ----------

// InferArch infers the CPU architecture from platform and os_version string.
// Used when the RegisterRequest proto doesn't carry an explicit arch field.
func InferArch(platform, osVersion string) string {
	switch platform {
	case "darwin", "macos":
		lower := strings.ToLower(osVersion)
		if strings.Contains(lower, "arm64") || strings.Contains(lower, "apple") {
			return "arm64"
		}
		return "amd64"
	case "windows":
		// arm64 Windows is extremely rare in healthcare SMBs.
		return "amd64"
	case "linux":
		lower := strings.ToLower(osVersion)
		if strings.Contains(lower, "aarch64") || strings.Contains(lower, "arm64") {
			return "arm64"
		}
		return "amd64"
	default:
		return "amd64"
	}
}

// NormalizeOSType maps the various OS type strings used across the codebase
// to the canonical platform name used by the manifest.
//
//	"macos" → "darwin"
//	"linux" → "linux"
//	"windows" → "windows"
func NormalizeOSType(osType string) string {
	switch strings.ToLower(osType) {
	case "macos", "darwin":
		return "darwin"
	case "linux":
		return "linux"
	case "windows":
		return "windows"
	default:
		return strings.ToLower(osType)
	}
}
