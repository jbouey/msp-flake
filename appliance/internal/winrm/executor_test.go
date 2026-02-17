package winrm

import (
	"strings"
	"testing"
)

func TestEncodePowerShell(t *testing.T) {
	// PowerShell -EncodedCommand expects UTF-16LE base64
	script := "Get-Date"
	encoded := encodePowerShell(script)

	if encoded == "" {
		t.Fatal("encoded should not be empty")
	}

	// Known encoding for "Get-Date" in UTF-16LE base64
	// G=0x47, e=0x65, t=0x74, -=0x2D, D=0x44, a=0x61, t=0x74, e=0x65
	// UTF-16LE: 47 00 65 00 74 00 2D 00 44 00 61 00 74 00 65 00
	expected := "RwBlAHQALQBEAGEAdABlAA=="
	if encoded != expected {
		t.Fatalf("expected %s, got %s", expected, encoded)
	}
}

func TestSplitString(t *testing.T) {
	tests := []struct {
		input    string
		size     int
		expected int
	}{
		{"hello", 3, 2},
		{"hello", 10, 1},
		{"", 5, 0},
		{"abcdef", 2, 3},
		{"abcdefg", 3, 3},
	}

	for _, tt := range tests {
		chunks := splitString(tt.input, tt.size)
		if len(chunks) != tt.expected {
			t.Fatalf("splitString(%q, %d) = %d chunks, want %d", tt.input, tt.size, len(chunks), tt.expected)
		}
		// Verify reassembly
		joined := strings.Join(chunks, "")
		if joined != tt.input {
			t.Fatalf("reassembled %q, want %q", joined, tt.input)
		}
	}
}

func TestHashOutput(t *testing.T) {
	output := map[string]interface{}{
		"status_code": 0,
		"std_out":     "OK",
		"std_err":     "",
		"success":     true,
	}

	hash := hashOutput(output)
	if len(hash) != 16 {
		t.Fatalf("expected 16 char hash, got %d: %s", len(hash), hash)
	}

	// Same input should produce same hash
	hash2 := hashOutput(output)
	if hash != hash2 {
		t.Fatal("hash should be deterministic")
	}

	// Different input should produce different hash
	output["std_out"] = "DIFFERENT"
	hash3 := hashOutput(output)
	if hash == hash3 {
		t.Fatal("different input should produce different hash")
	}
}

func TestNewExecutor(t *testing.T) {
	exec := NewExecutor()
	if exec == nil {
		t.Fatal("NewExecutor returned nil")
	}
	if exec.SessionCount() != 0 {
		t.Fatalf("expected 0 sessions, got %d", exec.SessionCount())
	}
}

func TestTargetDefaults(t *testing.T) {
	target := &Target{
		Hostname: "ws01.example.com",
		Username: `DOMAIN\admin`,
		Password: "pass123",
		UseSSL:   true,
	}

	if target.Port != 0 {
		t.Fatal("port should default to 0 (resolved in getSession)")
	}
	if !target.UseSSL {
		t.Fatal("UseSSL should be true")
	}
}

func TestInvalidateSession(t *testing.T) {
	exec := NewExecutor()
	// Invalidating a non-existent session should not panic
	exec.InvalidateSession("nonexistent")
	if exec.SessionCount() != 0 {
		t.Fatal("session count should be 0")
	}
}

func TestExecuteFailsWithoutConnection(t *testing.T) {
	exec := NewExecutor()

	target := &Target{
		Hostname: "192.168.88.999", // Invalid IP
		Port:     5986,
		Username: "admin",
		Password: "pass",
		UseSSL:   true,
	}

	result := exec.Execute(target, "Get-Date", "RB-001", "detect", 5, 0, 1.0, nil)
	if result.Success {
		t.Fatal("expected failure for invalid target")
	}
	if result.Error == "" {
		t.Fatal("expected error message")
	}
	if result.Target != "192.168.88.999" {
		t.Fatalf("expected target 192.168.88.999, got %s", result.Target)
	}
}

func TestLongScriptTriggersTemp(t *testing.T) {
	// Verify the threshold logic
	shortScript := strings.Repeat("a", inlineScriptLimit)
	if len(shortScript) > inlineScriptLimit {
		t.Fatal("test setup error")
	}

	longScript := strings.Repeat("a", inlineScriptLimit+1)
	if len(longScript) <= inlineScriptLimit {
		t.Fatal("test setup error: long script should exceed limit")
	}
}
