package daemon

import (
	"os"
	"path/filepath"
	"testing"
)

func TestNewManifest(t *testing.T) {
	dir := t.TempDir()
	m := NewAgentManifest(dir)

	if m == nil {
		t.Fatal("NewAgentManifest returned nil")
	}
	if m.Count() != 0 {
		t.Errorf("Empty manifest should have 0 entries, got %d", m.Count())
	}
}

func TestNewManifest_LoadsExisting(t *testing.T) {
	dir := t.TempDir()
	binDir := filepath.Join(dir, "bin")
	if err := os.MkdirAll(binDir, 0755); err != nil {
		t.Fatal(err)
	}

	// Pre-populate a manifest file.
	manifest := `{
		"entries": [
			{
				"platform": "linux",
				"arch": "amd64",
				"version": "0.3.0",
				"filename": "osiris-agent-linux-amd64",
				"sha256": "abc123",
				"size": 1024
			}
		],
		"updated_at": "2025-01-01T00:00:00Z"
	}`
	if err := os.WriteFile(filepath.Join(binDir, "manifest.json"), []byte(manifest), 0644); err != nil {
		t.Fatal(err)
	}

	m := NewAgentManifest(dir)
	if m.Count() != 1 {
		t.Errorf("Expected 1 entry from pre-existing manifest, got %d", m.Count())
	}

	entry := m.Lookup("linux", "amd64")
	if entry == nil {
		t.Fatal("Expected linux/amd64 entry")
	}
	if entry.Version != "0.3.0" {
		t.Errorf("Expected version 0.3.0, got %s", entry.Version)
	}
}

func TestScanDirectory(t *testing.T) {
	dir := t.TempDir()
	binDir := filepath.Join(dir, "bin")
	if err := os.MkdirAll(binDir, 0755); err != nil {
		t.Fatal(err)
	}

	// Create fake binaries with the naming convention.
	binaries := map[string]string{
		"osiris-agent-linux-amd64":  "linux-binary-contents",
		"osiris-agent-darwin-amd64": "darwin-binary-contents",
		"osiris-agent-darwin-arm64": "darwin-arm64-binary",
		"osiris-agent.exe":          "windows-binary-contents",
		"README.txt":                "not a binary",
		"some-other-file":           "also not a binary",
	}
	for name, content := range binaries {
		if err := os.WriteFile(filepath.Join(binDir, name), []byte(content), 0755); err != nil {
			t.Fatal(err)
		}
	}

	m := NewAgentManifest(dir)
	if err := m.ScanDirectory(binDir); err != nil {
		t.Fatalf("ScanDirectory: %v", err)
	}

	// Should have 4 binaries: linux/amd64, darwin/amd64, darwin/arm64, windows/amd64
	if m.Count() != 4 {
		t.Errorf("Expected 4 entries, got %d", m.Count())
	}

	tests := []struct {
		platform string
		arch     string
		filename string
	}{
		{"linux", "amd64", "osiris-agent-linux-amd64"},
		{"darwin", "amd64", "osiris-agent-darwin-amd64"},
		{"darwin", "arm64", "osiris-agent-darwin-arm64"},
		{"windows", "amd64", "osiris-agent.exe"},
	}

	for _, tt := range tests {
		entry := m.Lookup(tt.platform, tt.arch)
		if entry == nil {
			t.Errorf("Missing entry for %s/%s", tt.platform, tt.arch)
			continue
		}
		if entry.Filename != tt.filename {
			t.Errorf("%s/%s: filename = %q, want %q", tt.platform, tt.arch, entry.Filename, tt.filename)
		}
		if entry.SHA256 == "" {
			t.Errorf("%s/%s: SHA256 should not be empty", tt.platform, tt.arch)
		}
		if entry.Size == 0 {
			t.Errorf("%s/%s: size should not be 0", tt.platform, tt.arch)
		}
	}
}

func TestScanDirectory_Empty(t *testing.T) {
	dir := t.TempDir()
	emptyDir := filepath.Join(dir, "bin")
	if err := os.MkdirAll(emptyDir, 0755); err != nil {
		t.Fatal(err)
	}

	m := NewAgentManifest(dir)
	if err := m.ScanDirectory(emptyDir); err != nil {
		t.Fatalf("ScanDirectory on empty dir: %v", err)
	}
	if m.Count() != 0 {
		t.Errorf("Empty directory should yield 0 entries, got %d", m.Count())
	}
}

func TestScanDirectory_Nonexistent(t *testing.T) {
	dir := t.TempDir()
	m := NewAgentManifest(dir)

	// Scanning a directory that doesn't exist should not error (just no results).
	if err := m.ScanDirectory(filepath.Join(dir, "nonexistent")); err != nil {
		t.Fatalf("ScanDirectory on nonexistent dir should not error, got: %v", err)
	}
	if m.Count() != 0 {
		t.Errorf("Nonexistent directory should yield 0 entries, got %d", m.Count())
	}
}

func TestScanDirectory_Idempotent(t *testing.T) {
	dir := t.TempDir()
	binDir := filepath.Join(dir, "bin")
	if err := os.MkdirAll(binDir, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(binDir, "osiris-agent-linux-amd64"), []byte("binary"), 0755); err != nil {
		t.Fatal(err)
	}

	m := NewAgentManifest(dir)

	// Scan twice — should produce the same result.
	if err := m.ScanDirectory(binDir); err != nil {
		t.Fatal(err)
	}
	count1 := m.Count()
	sha1 := m.Lookup("linux", "amd64").SHA256

	if err := m.ScanDirectory(binDir); err != nil {
		t.Fatal(err)
	}
	count2 := m.Count()
	sha2 := m.Lookup("linux", "amd64").SHA256

	if count1 != count2 {
		t.Errorf("Idempotent scan: count changed from %d to %d", count1, count2)
	}
	if sha1 != sha2 {
		t.Errorf("Idempotent scan: SHA256 changed")
	}
}

func TestLookup(t *testing.T) {
	dir := t.TempDir()
	m := NewAgentManifest(dir)

	m.Register(AgentBinary{
		Platform: "linux",
		Arch:     "amd64",
		Version:  "0.4.0",
		Filename: "osiris-agent-linux-amd64",
		SHA256:   "deadbeef",
		Size:     2048,
	})
	m.Register(AgentBinary{
		Platform: "darwin",
		Arch:     "arm64",
		Version:  "0.4.0",
		Filename: "osiris-agent-darwin-arm64",
		SHA256:   "cafebabe",
		Size:     3072,
	})

	entry := m.Lookup("linux", "amd64")
	if entry == nil {
		t.Fatal("Lookup linux/amd64 returned nil")
	}
	if entry.Version != "0.4.0" {
		t.Errorf("Version = %q, want %q", entry.Version, "0.4.0")
	}

	entry = m.Lookup("darwin", "arm64")
	if entry == nil {
		t.Fatal("Lookup darwin/arm64 returned nil")
	}
	if entry.SHA256 != "cafebabe" {
		t.Errorf("SHA256 = %q, want %q", entry.SHA256, "cafebabe")
	}
}

func TestLookupMissing(t *testing.T) {
	dir := t.TempDir()
	m := NewAgentManifest(dir)

	// Lookup on empty manifest should return nil, not panic.
	if entry := m.Lookup("linux", "amd64"); entry != nil {
		t.Errorf("Empty manifest lookup should return nil, got: %+v", entry)
	}

	// Lookup for nonexistent platform/arch after registering others.
	m.Register(AgentBinary{
		Platform: "linux",
		Arch:     "amd64",
		Filename: "osiris-agent-linux-amd64",
	})
	if entry := m.Lookup("freebsd", "amd64"); entry != nil {
		t.Errorf("Unknown platform lookup should return nil, got: %+v", entry)
	}
	if entry := m.Lookup("linux", "arm64"); entry != nil {
		t.Errorf("Unknown arch lookup should return nil, got: %+v", entry)
	}
}

func TestLookupCompatible(t *testing.T) {
	dir := t.TempDir()
	m := NewAgentManifest(dir)

	m.Register(AgentBinary{
		Platform:     "darwin",
		Arch:         "amd64",
		Version:      "0.4.1",
		Filename:     "osiris-agent-darwin-amd64",
		MinOSVersion: "12.0",
	})
	m.Register(AgentBinary{
		Platform: "linux",
		Arch:     "amd64",
		Version:  "0.4.1",
		Filename: "osiris-agent-linux-amd64",
		// No MinOSVersion — always compatible.
	})

	tests := []struct {
		name      string
		platform  string
		arch      string
		osVersion string
		wantNil   bool
	}{
		{"macOS 14 compatible", "darwin", "amd64", "14.2.1", false},
		{"macOS 12 compatible", "darwin", "amd64", "12.0", false},
		{"macOS 11 too old", "darwin", "amd64", "11.7.3", true},
		{"macOS 10 too old", "darwin", "amd64", "10.15.7", true},
		{"macOS empty version (best effort)", "darwin", "amd64", "", false},
		{"linux always compatible", "linux", "amd64", "Ubuntu 22.04", false},
		{"linux empty version", "linux", "amd64", "", false},
		{"missing platform", "freebsd", "amd64", "", true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			entry := m.LookupCompatible(tt.platform, tt.arch, tt.osVersion)
			if tt.wantNil && entry != nil {
				t.Errorf("Expected nil, got: %+v", entry)
			}
			if !tt.wantNil && entry == nil {
				t.Error("Expected entry, got nil")
			}
		})
	}
}

func TestRegister(t *testing.T) {
	dir := t.TempDir()
	binDir := filepath.Join(dir, "bin")
	if err := os.MkdirAll(binDir, 0755); err != nil {
		t.Fatal(err)
	}

	m := NewAgentManifest(dir)

	err := m.Register(AgentBinary{
		Platform: "linux",
		Arch:     "amd64",
		Version:  "0.4.0",
		Filename: "osiris-agent-linux-amd64",
		SHA256:   "abc123",
		Size:     1024,
	})
	if err != nil {
		t.Fatalf("Register: %v", err)
	}

	if m.Count() != 1 {
		t.Errorf("After register: count = %d, want 1", m.Count())
	}

	// Verify persistence.
	manifestPath := filepath.Join(binDir, "manifest.json")
	if _, err := os.Stat(manifestPath); os.IsNotExist(err) {
		t.Error("Register should persist manifest.json to disk")
	}

	// Update same entry.
	err = m.Register(AgentBinary{
		Platform: "linux",
		Arch:     "amd64",
		Version:  "0.4.1",
		Filename: "osiris-agent-linux-amd64",
		SHA256:   "def456",
		Size:     2048,
	})
	if err != nil {
		t.Fatalf("Register update: %v", err)
	}

	if m.Count() != 1 {
		t.Errorf("Update should not increase count: got %d", m.Count())
	}

	entry := m.Lookup("linux", "amd64")
	if entry.Version != "0.4.1" {
		t.Errorf("After update: version = %q, want %q", entry.Version, "0.4.1")
	}
}

func TestRegister_Validation(t *testing.T) {
	dir := t.TempDir()
	binDir := filepath.Join(dir, "bin")
	os.MkdirAll(binDir, 0755)
	m := NewAgentManifest(dir)

	if err := m.Register(AgentBinary{Filename: "test"}); err == nil {
		t.Error("Register with empty platform/arch should fail")
	}
	if err := m.Register(AgentBinary{Platform: "linux", Arch: "amd64"}); err == nil {
		t.Error("Register with empty filename should fail")
	}
}

func TestSaveLoad(t *testing.T) {
	dir := t.TempDir()
	binDir := filepath.Join(dir, "bin")
	if err := os.MkdirAll(binDir, 0755); err != nil {
		t.Fatal(err)
	}

	m := NewAgentManifest(dir)

	entries := []AgentBinary{
		{Platform: "linux", Arch: "amd64", Version: "0.4.0", Filename: "osiris-agent-linux-amd64", SHA256: "aaa", Size: 1000},
		{Platform: "darwin", Arch: "amd64", Version: "0.4.0", Filename: "osiris-agent-darwin-amd64", SHA256: "bbb", Size: 2000},
		{Platform: "darwin", Arch: "arm64", Version: "0.4.0", Filename: "osiris-agent-darwin-arm64", SHA256: "ccc", Size: 3000, MinOSVersion: "12.0"},
		{Platform: "windows", Arch: "amd64", Version: "0.4.0", Filename: "osiris-agent.exe", SHA256: "ddd", Size: 4000},
	}

	for _, e := range entries {
		if err := m.Register(e); err != nil {
			t.Fatalf("Register %s/%s: %v", e.Platform, e.Arch, err)
		}
	}

	// Save explicitly.
	if err := m.Save(); err != nil {
		t.Fatalf("Save: %v", err)
	}

	// Create a new manifest from the same dir — should load persisted data.
	m2 := NewAgentManifest(dir)
	if m2.Count() != 4 {
		t.Errorf("Loaded manifest count = %d, want 4", m2.Count())
	}

	// Verify round-trip fidelity.
	for _, e := range entries {
		loaded := m2.Lookup(e.Platform, e.Arch)
		if loaded == nil {
			t.Errorf("Missing %s/%s after load", e.Platform, e.Arch)
			continue
		}
		if loaded.SHA256 != e.SHA256 {
			t.Errorf("%s/%s: SHA256 = %q, want %q", e.Platform, e.Arch, loaded.SHA256, e.SHA256)
		}
		if loaded.Size != e.Size {
			t.Errorf("%s/%s: Size = %d, want %d", e.Platform, e.Arch, loaded.Size, e.Size)
		}
		if loaded.MinOSVersion != e.MinOSVersion {
			t.Errorf("%s/%s: MinOSVersion = %q, want %q", e.Platform, e.Arch, loaded.MinOSVersion, e.MinOSVersion)
		}
	}
}

func TestLoad_CorruptJSON(t *testing.T) {
	dir := t.TempDir()
	binDir := filepath.Join(dir, "bin")
	if err := os.MkdirAll(binDir, 0755); err != nil {
		t.Fatal(err)
	}

	// Write corrupt JSON.
	if err := os.WriteFile(filepath.Join(binDir, "manifest.json"), []byte("{corrupt"), 0644); err != nil {
		t.Fatal(err)
	}

	// Should not panic; returns an error and starts with an empty manifest.
	m := NewAgentManifest(dir)
	if m.Count() != 0 {
		t.Errorf("Corrupt JSON should result in empty manifest, got %d entries", m.Count())
	}
}

func TestLoad_SkipsCorruptEntries(t *testing.T) {
	dir := t.TempDir()
	binDir := filepath.Join(dir, "bin")
	if err := os.MkdirAll(binDir, 0755); err != nil {
		t.Fatal(err)
	}

	// Manifest with one valid and one corrupt entry (missing platform).
	manifest := `{
		"entries": [
			{"platform": "linux", "arch": "amd64", "filename": "agent", "sha256": "abc"},
			{"platform": "", "arch": "amd64", "filename": "bad", "sha256": "def"}
		]
	}`
	if err := os.WriteFile(filepath.Join(binDir, "manifest.json"), []byte(manifest), 0644); err != nil {
		t.Fatal(err)
	}

	m := NewAgentManifest(dir)
	if m.Count() != 1 {
		t.Errorf("Expected 1 valid entry (corrupt skipped), got %d", m.Count())
	}
}

func TestEntries(t *testing.T) {
	dir := t.TempDir()
	m := NewAgentManifest(dir)
	m.Register(AgentBinary{Platform: "linux", Arch: "amd64", Filename: "a"})
	m.Register(AgentBinary{Platform: "darwin", Arch: "amd64", Filename: "b"})

	entries := m.Entries()
	if len(entries) != 2 {
		t.Errorf("Entries() returned %d, want 2", len(entries))
	}

	// Verify it's a copy (mutating shouldn't affect the manifest).
	entries[0].Version = "mutated"
	original := m.Lookup(entries[0].Platform, entries[0].Arch)
	if original.Version == "mutated" {
		t.Error("Entries() should return copies, not references")
	}
}

// ---------- filename parsing ----------

func TestParseBinaryFilename(t *testing.T) {
	tests := []struct {
		name         string
		filename     string
		wantPlatform string
		wantArch     string
	}{
		{"linux amd64", "osiris-agent-linux-amd64", "linux", "amd64"},
		{"linux arm64", "osiris-agent-linux-arm64", "linux", "arm64"},
		{"darwin amd64", "osiris-agent-darwin-amd64", "darwin", "amd64"},
		{"darwin arm64", "osiris-agent-darwin-arm64", "darwin", "arm64"},
		{"windows exe", "osiris-agent.exe", "windows", "amd64"},
		{"unknown platform", "osiris-agent-freebsd-amd64", "", ""},
		{"unknown arch", "osiris-agent-linux-mips", "", ""},
		{"random file", "README.txt", "", ""},
		{"partial match", "osiris-agent-linux", "", ""},
		{"no prefix", "agent-linux-amd64", "", ""},
		{"empty", "", "", ""},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			platform, arch := parseBinaryFilename(tt.filename)
			if platform != tt.wantPlatform || arch != tt.wantArch {
				t.Errorf("parseBinaryFilename(%q) = (%q, %q), want (%q, %q)",
					tt.filename, platform, arch, tt.wantPlatform, tt.wantArch)
			}
		})
	}
}

// ---------- version comparison ----------

func TestVersionAtLeast(t *testing.T) {
	tests := []struct {
		hostVer string
		minVer  string
		want    bool
	}{
		{"14.2.1", "12.0", true},
		{"12.0", "12.0", true},
		{"11.7.3", "12.0", false},
		{"10.15.7", "12.0", false},
		{"13.0", "12.0", true},
		{"12.1", "12.0", true},
		{"12.0.1", "12.0", true},
		{"11.0", "12.0", false},
		{"22.04", "20.04", true},
		{"20.04", "22.04", false},
		{"1.0", "1.0", true},
		{"2.0", "1.0", true},
	}

	for _, tt := range tests {
		t.Run(tt.hostVer+">="+tt.minVer, func(t *testing.T) {
			got := versionAtLeast(tt.hostVer, tt.minVer)
			if got != tt.want {
				t.Errorf("versionAtLeast(%q, %q) = %v, want %v", tt.hostVer, tt.minVer, got, tt.want)
			}
		})
	}
}

func TestVersionAtLeast_WithPrefix(t *testing.T) {
	// OS version strings sometimes have non-numeric prefixes.
	if !versionAtLeast("macOS 14.2", "12.0") {
		t.Error("Should handle 'macOS 14.2' prefix")
	}
	if versionAtLeast("macOS 11.0", "12.0") {
		t.Error("macOS 11.0 should not satisfy min 12.0")
	}
}

// ---------- platform detection ----------

func TestInferArch(t *testing.T) {
	tests := []struct {
		name     string
		platform string
		osVer    string
		want     string
	}{
		{"macOS Intel", "darwin", "macOS 14.2.1 (23C71)", "amd64"},
		{"macOS Apple Silicon", "darwin", "macOS 14.2 arm64", "arm64"},
		{"macOS Apple keyword", "darwin", "Apple M1 macOS 13.0", "arm64"},
		{"macOS empty", "darwin", "", "amd64"},
		{"windows", "windows", "Windows 10 Pro", "amd64"},
		{"windows empty", "windows", "", "amd64"},
		{"linux amd64", "linux", "Ubuntu 22.04", "amd64"},
		{"linux aarch64", "linux", "aarch64 Linux 6.1", "arm64"},
		{"linux arm64", "linux", "Debian arm64", "arm64"},
		{"linux empty", "linux", "", "amd64"},
		{"unknown platform", "unknown", "", "amd64"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := InferArch(tt.platform, tt.osVer)
			if got != tt.want {
				t.Errorf("InferArch(%q, %q) = %q, want %q", tt.platform, tt.osVer, got, tt.want)
			}
		})
	}
}

// ---------- NormalizeOSType ----------

func TestNormalizeOSType(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"macos", "darwin"},
		{"darwin", "darwin"},
		{"linux", "linux"},
		{"windows", "windows"},
		{"MacOS", "darwin"},
		{"LINUX", "linux"},
		{"Windows", "windows"},
	}

	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			got := NormalizeOSType(tt.input)
			if got != tt.want {
				t.Errorf("NormalizeOSType(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}

// ---------- getAgentBinary ----------

func TestGetAgentBinary_ManifestHit(t *testing.T) {
	dir := t.TempDir()
	binDir := filepath.Join(dir, "bin")
	if err := os.MkdirAll(binDir, 0755); err != nil {
		t.Fatal(err)
	}

	// Create actual binary file.
	binaryPath := filepath.Join(binDir, "osiris-agent-linux-amd64")
	if err := os.WriteFile(binaryPath, []byte("binary-data"), 0755); err != nil {
		t.Fatal(err)
	}

	d := &Daemon{
		config:        &Config{StateDir: dir},
		agentManifest: NewAgentManifest(dir),
	}
	d.agentManifest.Register(AgentBinary{
		Platform: "linux",
		Arch:     "amd64",
		Version:  "0.4.1",
		Filename: "osiris-agent-linux-amd64",
		SHA256:   "abc",
		Size:     11,
	})

	path, meta, err := d.getAgentBinary("linux", "amd64", "")
	if err != nil {
		t.Fatalf("getAgentBinary: %v", err)
	}
	if path != binaryPath {
		t.Errorf("path = %q, want %q", path, binaryPath)
	}
	if meta == nil {
		t.Fatal("Expected non-nil metadata")
	}
	if meta.Version != "0.4.1" {
		t.Errorf("version = %q, want %q", meta.Version, "0.4.1")
	}
}

func TestGetAgentBinary_FallbackToLegacy(t *testing.T) {
	dir := t.TempDir()
	binDir := filepath.Join(dir, "bin")
	if err := os.MkdirAll(binDir, 0755); err != nil {
		t.Fatal(err)
	}

	// Create legacy-named binary file (no manifest entry).
	legacyPath := filepath.Join(binDir, "osiris-agent-linux-amd64")
	if err := os.WriteFile(legacyPath, []byte("legacy-binary"), 0755); err != nil {
		t.Fatal(err)
	}

	d := &Daemon{
		config:        &Config{StateDir: dir},
		agentManifest: NewAgentManifest(dir), // empty manifest
	}

	path, meta, err := d.getAgentBinary("linux", "amd64", "")
	if err != nil {
		t.Fatalf("getAgentBinary fallback: %v", err)
	}
	if path != legacyPath {
		t.Errorf("path = %q, want %q", path, legacyPath)
	}
	if meta != nil {
		t.Errorf("Legacy fallback should return nil metadata, got: %+v", meta)
	}
}

func TestGetAgentBinary_NilManifest(t *testing.T) {
	dir := t.TempDir()
	binDir := filepath.Join(dir, "bin")
	if err := os.MkdirAll(binDir, 0755); err != nil {
		t.Fatal(err)
	}

	// Create legacy binary.
	if err := os.WriteFile(filepath.Join(binDir, "osiris-agent-darwin-amd64"), []byte("binary"), 0755); err != nil {
		t.Fatal(err)
	}

	d := &Daemon{
		config:        &Config{StateDir: dir},
		agentManifest: nil, // nil manifest — should still work via legacy fallback
	}

	path, _, err := d.getAgentBinary("darwin", "amd64", "")
	if err != nil {
		t.Fatalf("getAgentBinary with nil manifest: %v", err)
	}
	if path == "" {
		t.Error("Expected non-empty path")
	}
}

func TestGetAgentBinary_Incompatible(t *testing.T) {
	dir := t.TempDir()
	binDir := filepath.Join(dir, "bin")
	if err := os.MkdirAll(binDir, 0755); err != nil {
		t.Fatal(err)
	}

	d := &Daemon{
		config:        &Config{StateDir: dir},
		agentManifest: NewAgentManifest(dir),
	}
	d.agentManifest.Register(AgentBinary{
		Platform:     "darwin",
		Arch:         "amd64",
		Filename:     "osiris-agent-darwin-amd64",
		MinOSVersion: "12.0",
	})

	// macOS 11 is too old — manifest returns nil, fallback also fails (no file).
	_, _, err := d.getAgentBinary("darwin", "amd64", "11.0")
	if err == nil {
		t.Error("Expected error for incompatible OS version with no fallback binary")
	}
}

// ---------- legacyOSType ----------

func TestLegacyOSType(t *testing.T) {
	tests := []struct {
		platform string
		want     string
	}{
		{"darwin", "macos"},
		{"linux", "linux"},
		{"windows", "windows"},
	}
	for _, tt := range tests {
		got := legacyOSType(tt.platform)
		if got != tt.want {
			t.Errorf("legacyOSType(%q) = %q, want %q", tt.platform, got, tt.want)
		}
	}
}
