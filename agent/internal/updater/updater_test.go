package updater

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

func TestDownloadBinary_SetsExecutePermission(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("file permissions not meaningful on Windows")
	}

	// Create a fake binary to serve
	fakeContent := []byte("#!/bin/sh\necho hello\n")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write(fakeContent)
	}))
	defer srv.Close()

	tmpDir := t.TempDir()
	destPath := filepath.Join(tmpDir, "osiris-agent.new")

	u := New(tmpDir, tmpDir, "0.0.1", "test-service")

	if err := u.downloadBinary(context.Background(), srv.URL+"/agent", destPath); err != nil {
		t.Fatalf("downloadBinary failed: %v", err)
	}

	info, err := os.Stat(destPath)
	if err != nil {
		t.Fatalf("stat downloaded file: %v", err)
	}

	perm := info.Mode().Perm()
	if perm&0111 == 0 {
		t.Errorf("downloaded binary is not executable: got %o, want 0755", perm)
	}
	if perm != 0755 {
		t.Errorf("unexpected permissions: got %o, want 0755", perm)
	}
}

func TestDownloadBinary_ContentIntact(t *testing.T) {
	fakeContent := []byte("fake-binary-content-1234567890")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write(fakeContent)
	}))
	defer srv.Close()

	tmpDir := t.TempDir()
	destPath := filepath.Join(tmpDir, "osiris-agent.new")

	u := New(tmpDir, tmpDir, "0.0.1", "test-service")
	if err := u.downloadBinary(context.Background(), srv.URL+"/agent", destPath); err != nil {
		t.Fatalf("downloadBinary failed: %v", err)
	}

	got, err := os.ReadFile(destPath)
	if err != nil {
		t.Fatalf("read downloaded file: %v", err)
	}
	if string(got) != string(fakeContent) {
		t.Errorf("content mismatch: got %d bytes, want %d bytes", len(got), len(fakeContent))
	}
}

func TestDownloadBinary_HTTPError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
	}))
	defer srv.Close()

	tmpDir := t.TempDir()
	destPath := filepath.Join(tmpDir, "osiris-agent.new")

	u := New(tmpDir, tmpDir, "0.0.1", "test-service")
	err := u.downloadBinary(nil, srv.URL+"/agent", destPath)
	if err == nil {
		t.Fatal("expected error for HTTP 404, got nil")
	}

	// File should not exist after failed download
	if _, statErr := os.Stat(destPath); !os.IsNotExist(statErr) {
		t.Error("file should not exist after failed download")
	}
}

func TestApplyUpdate_PreservesExecutePermission(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("file permissions not meaningful on Windows")
	}

	tmpDir := t.TempDir()
	installDir := filepath.Join(tmpDir, "install")
	dataDir := filepath.Join(tmpDir, "data")
	os.MkdirAll(installDir, 0755)
	os.MkdirAll(dataDir, 0755)

	binName := "osiris-agent"
	if runtime.GOOS == "windows" {
		binName = "osiris-agent.exe"
	}

	// Create "current" binary
	currentPath := filepath.Join(installDir, binName)
	os.WriteFile(currentPath, []byte("old-binary"), 0755)

	// Create ".new" binary (simulating a download with correct perms)
	newPath := filepath.Join(installDir, binName+".new")
	os.WriteFile(newPath, []byte("new-binary"), 0755)

	u := New(dataDir, installDir, "0.0.1", "test-service")

	// applyUpdate renames .new → current. The permissions should transfer.
	err := u.applyUpdate("0.0.2", fakeSHA256("new-binary"))
	if err != nil {
		// restartService will fail in test — that's expected.
		// The rename should have succeeded though.
		_ = err
	}

	info, err := os.Stat(currentPath)
	if err != nil {
		t.Fatalf("stat current binary after update: %v", err)
	}

	// Verify the new content is in place
	data, _ := os.ReadFile(currentPath)
	if string(data) != "new-binary" {
		t.Errorf("binary content not updated: got %q", string(data))
	}

	// On Unix, renamed file keeps source permissions
	perm := info.Mode().Perm()
	if perm&0111 == 0 {
		t.Errorf("updated binary lost execute permission: got %o", perm)
	}
}

func TestBinaryName(t *testing.T) {
	name := binaryName()
	if runtime.GOOS == "windows" {
		if name != "osiris-agent.exe" {
			t.Errorf("expected osiris-agent.exe on Windows, got %s", name)
		}
	} else {
		if name != "osiris-agent" {
			t.Errorf("expected osiris-agent on %s, got %s", runtime.GOOS, name)
		}
	}
}

func fakeSHA256(content string) string {
	h := sha256.Sum256([]byte(content))
	return hex.EncodeToString(h[:])
}

func TestFileSHA256(t *testing.T) {
	tmpDir := t.TempDir()
	content := []byte("test-content-for-hashing")
	path := filepath.Join(tmpDir, "testfile")
	os.WriteFile(path, content, 0644)

	got, err := fileSHA256(path)
	if err != nil {
		t.Fatalf("fileSHA256 failed: %v", err)
	}

	expected := sha256.Sum256(content)
	expectedHex := hex.EncodeToString(expected[:])
	if got != expectedHex {
		t.Errorf("SHA256 mismatch: got %s, want %s", got, expectedHex)
	}
}

func TestNew(t *testing.T) {
	u := New("/data", "/install", "1.0.0", "svc")
	if u.dataDir != "/data" {
		t.Errorf("dataDir: got %s, want /data", u.dataDir)
	}
	if u.installDir != "/install" {
		t.Errorf("installDir: got %s, want /install", u.installDir)
	}
	if u.currentVersion != "1.0.0" {
		t.Errorf("currentVersion: got %s, want 1.0.0", u.currentVersion)
	}
	if u.httpClient == nil {
		t.Error("httpClient should not be nil")
	}
}

func TestCheckAndUpdate_RejectsInvalidURL(t *testing.T) {
	u := New(t.TempDir(), t.TempDir(), "1.0.0", "svc")
	err := u.CheckAndUpdate(nil, "2.0.0", "not-a-valid-url", "abc123")
	if err == nil {
		t.Error("expected error for invalid URL")
	}
}

func TestCheckAndUpdate_RejectsSameVersion(t *testing.T) {
	u := New(t.TempDir(), t.TempDir(), "1.0.0", "svc")
	err := u.CheckAndUpdate(nil, "1.0.0", "http://localhost/agent", "abc123")
	if err == nil {
		t.Error("expected error when update version matches current version")
	}
}

func TestCheckAndUpdate_ConcurrentGuard(t *testing.T) {
	u := New(t.TempDir(), t.TempDir(), "1.0.0", "svc")
	u.mu.Lock()
	u.updating = true
	u.mu.Unlock()

	err := u.CheckAndUpdate(nil, "2.0.0", "http://localhost/agent", "abc123")
	if err == nil {
		t.Error("expected error when update already in progress")
	}
	if err.Error() != "update already in progress" {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestCheckAndUpdate_BackoffAfterFailure(t *testing.T) {
	u := New(t.TempDir(), t.TempDir(), "1.0.0", "svc")
	u.failureCount = 1
	// lastFailure is zero time, so duration since will be huge, no backoff
	// Set it to now to trigger backoff
	u.lastFailure = time.Now()

	err := u.CheckAndUpdate(nil, "2.0.0", "http://localhost/agent", "abc123")
	if err == nil {
		t.Error("expected backoff error")
	}
}

func TestCheckAndUpdate_SHA256Mismatch(t *testing.T) {
	content := []byte("fake-binary")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write(content)
	}))
	defer srv.Close()

	installDir := t.TempDir()
	dataDir := t.TempDir()

	// Create current binary so rename works
	binName := binaryName()
	os.WriteFile(filepath.Join(installDir, binName), []byte("old"), 0755)

	u := New(dataDir, installDir, "1.0.0", "svc")

	err := u.CheckAndUpdate(nil, "2.0.0", fmt.Sprintf("%s/agent/%s", srv.URL, binName), "wrong-sha256")
	if err == nil {
		t.Error("expected SHA256 mismatch error")
	}
}

func TestValidateBinaryPlatform_MatchesCurrentOS(t *testing.T) {
	tmpDir := t.TempDir()
	path := filepath.Join(tmpDir, "testbin")

	var magic []byte
	switch runtime.GOOS {
	case "darwin":
		magic = []byte{0xCF, 0xFA, 0xED, 0xFE, 0, 0, 0, 0}
	case "linux":
		magic = []byte{0x7F, 'E', 'L', 'F', 0, 0, 0, 0}
	case "windows":
		magic = []byte{'M', 'Z', 0, 0, 0, 0, 0, 0}
	default:
		t.Skipf("unsupported OS: %s", runtime.GOOS)
	}
	os.WriteFile(path, magic, 0644)

	if err := validateBinaryPlatform(path); err != nil {
		t.Errorf("expected no error for matching platform, got: %v", err)
	}
}

func TestValidateBinaryPlatform_RejectsWrongPlatform(t *testing.T) {
	tmpDir := t.TempDir()

	tests := []struct {
		name  string
		magic []byte
		skip  string
	}{
		{"PE_on_non_windows", []byte{'M', 'Z', 0, 0, 0, 0, 0, 0}, "windows"},
		{"MachO_on_non_darwin", []byte{0xCF, 0xFA, 0xED, 0xFE, 0, 0, 0, 0}, "darwin"},
		{"ELF_on_non_linux", []byte{0x7F, 'E', 'L', 'F', 0, 0, 0, 0}, "linux"},
		{"universal_on_non_darwin", []byte{0xCA, 0xFE, 0xBA, 0xBE, 0, 0, 0, 0}, "darwin"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if runtime.GOOS == tt.skip {
				t.Skipf("binary matches current OS %s", tt.skip)
			}
			path := filepath.Join(tmpDir, tt.name)
			os.WriteFile(path, tt.magic, 0644)

			err := validateBinaryPlatform(path)
			if err == nil {
				t.Error("expected platform mismatch error")
			}
		})
	}
}

func TestValidateBinaryPlatform_UnrecognizedFormat(t *testing.T) {
	tmpDir := t.TempDir()
	path := filepath.Join(tmpDir, "garbage")
	os.WriteFile(path, []byte{0xFF, 0xFF, 0xFF, 0xFF}, 0644)

	err := validateBinaryPlatform(path)
	if err == nil {
		t.Error("expected error for unrecognized format")
	}
}

func TestValidateBinaryPlatform_EmptyFile(t *testing.T) {
	tmpDir := t.TempDir()
	path := filepath.Join(tmpDir, "empty")
	os.WriteFile(path, []byte{}, 0644)

	err := validateBinaryPlatform(path)
	if err == nil {
		t.Error("expected error for empty file")
	}
}
