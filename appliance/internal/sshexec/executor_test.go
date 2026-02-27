package sshexec

import (
	"context"
	"fmt"
	"testing"
	"time"
)

func TestNewExecutor(t *testing.T) {
	exec := NewExecutor()
	if exec == nil {
		t.Fatal("NewExecutor returned nil")
	}
	if exec.ConnectionCount() != 0 {
		t.Fatalf("expected 0 connections, got %d", exec.ConnectionCount())
	}
}

func TestBuildSSHConfigKey(t *testing.T) {
	key := `-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW
QyNTUxOQAAACDW8v/Qu5OkJPU0PDsXum2lhfmj5lYrgyZ7I7S3v5y1RwAAAJg5rVO/Oa1T
vwAAAAtzc2gtZWQyNTUxOQAAACDW8v/Qu5OkJPU0PDsXum2lhfmj5lYrgyZ7I7S3v5y1Rw
AAAEAuJ7pAsbywtyQ+v7e4TlzUy8ojcPdo8dzibkW6uODXOdby/9C7k6Qk9TQ8Oxe6baWF
+aPmViuDJnsjtLe/nLVHAAAAE2RhZEBNQUxBQ0hPUjUubG9jYWwBAg==
-----END OPENSSH PRIVATE KEY-----`

	target := &Target{
		Hostname:   "test.example.com",
		Username:   "admin",
		PrivateKey: &key,
	}

	config, err := NewExecutor().buildSSHConfig(target)
	if err != nil {
		t.Fatalf("buildSSHConfig with key: %v", err)
	}
	if config.User != "admin" {
		t.Fatalf("expected user=admin, got %s", config.User)
	}
	if len(config.Auth) != 1 {
		t.Fatalf("expected 1 auth method, got %d", len(config.Auth))
	}
}

func TestBuildSSHConfigPassword(t *testing.T) {
	pass := "secret"
	target := &Target{
		Hostname: "test.example.com",
		Username: "root",
		Password: &pass,
	}

	config, err := NewExecutor().buildSSHConfig(target)
	if err != nil {
		t.Fatalf("buildSSHConfig with password: %v", err)
	}
	if config.User != "root" {
		t.Fatalf("expected user=root, got %s", config.User)
	}
}

func TestBuildSSHConfigNoAuth(t *testing.T) {
	target := &Target{
		Hostname: "test.example.com",
		Username: "root",
	}

	_, err := NewExecutor().buildSSHConfig(target)
	if err == nil {
		t.Fatal("expected error for missing auth")
	}
}

func TestBuildSSHConfigDefaultUser(t *testing.T) {
	pass := "secret"
	target := &Target{
		Hostname: "test.example.com",
		Password: &pass,
	}

	config, err := NewExecutor().buildSSHConfig(target)
	if err != nil {
		t.Fatalf("buildSSHConfig: %v", err)
	}
	if config.User != "root" {
		t.Fatalf("expected default user=root, got %s", config.User)
	}
}

func TestIsAuthError(t *testing.T) {
	tests := []struct {
		msg  string
		want bool
	}{
		{"unable to authenticate", true},
		{"ssh: permission denied (publickey)", true},
		{"no supported methods remain", true},
		{"connection refused", false},
		{"timeout", false},
		{"", false},
	}

	for _, tt := range tests {
		err := fmt.Errorf("%s", tt.msg)
		if isAuthError(err) != tt.want {
			t.Errorf("isAuthError(%q) = %v, want %v", tt.msg, !tt.want, tt.want)
		}
	}
}

func TestHashOutput(t *testing.T) {
	output := map[string]interface{}{
		"stdout":    "hello",
		"stderr":    "",
		"exit_code": 0,
		"success":   true,
	}

	hash := hashOutput(output)
	if len(hash) != 16 {
		t.Fatalf("expected 16 char hash, got %d", len(hash))
	}

	// Deterministic
	if hash != hashOutput(output) {
		t.Fatal("hash should be deterministic")
	}
}

func TestInvalidateConnection(t *testing.T) {
	exec := NewExecutor()
	// Should not panic on nonexistent
	exec.InvalidateConnection("nonexistent")
	if exec.ConnectionCount() != 0 {
		t.Fatal("expected 0 connections")
	}
}

func TestExecuteFailsWithBadHost(t *testing.T) {
	exec := NewExecutor()
	pass := "pass"
	target := &Target{
		Hostname:       "192.168.88.999",
		Port:           22,
		Username:       "root",
		Password:       &pass,
		ConnectTimeout: 2,
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	result := exec.Execute(ctx, target, "echo hello", "RB-001", "detect", 5, 0, 1.0, false, nil)
	if result.Success {
		t.Fatal("expected failure for invalid target")
	}
	if result.Error == "" {
		t.Fatal("expected error message")
	}
	if result.ExitCode != -1 {
		t.Fatalf("expected exit code -1, got %d", result.ExitCode)
	}
}

func TestFailResult(t *testing.T) {
	start := time.Now()
	result := failResult("RB-001", "ws01", "remediate", "timeout", start, 2, []string{"164.312(b)"}, "ubuntu")

	if result.Success {
		t.Fatal("expected failure")
	}
	if result.RunbookID != "RB-001" {
		t.Fatalf("expected RB-001, got %s", result.RunbookID)
	}
	if result.RetryCount != 2 {
		t.Fatalf("expected 2 retries, got %d", result.RetryCount)
	}
	if result.Distro != "ubuntu" {
		t.Fatalf("expected ubuntu, got %s", result.Distro)
	}
	if result.ExitCode != -1 {
		t.Fatalf("expected -1, got %d", result.ExitCode)
	}
}

func TestCloseAll(t *testing.T) {
	exec := NewExecutor()
	exec.CloseAll() // Should not panic on empty
	if exec.ConnectionCount() != 0 {
		t.Fatal("expected 0 connections after CloseAll")
	}
}

