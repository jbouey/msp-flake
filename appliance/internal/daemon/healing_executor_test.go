package daemon

import "testing"

func TestClassifyHealError(t *testing.T) {
	tests := []struct {
		name     string
		errMsg   string
		expected string
	}{
		{"empty", "", ""},
		{"401 status", "HTTP 401 response", "auth_failure"},
		{"unauthorized", "Unauthorized access to resource", "auth_failure"},
		{"access denied", "Access Denied by policy", "auth_failure"},
		{"permission denied", "permission denied for user", "auth_failure"},
		{"timeout", "context deadline exceeded", "timeout"},
		{"timed out", "operation timed out after 30s", "timeout"},
		{"deadline exceeded", "Deadline Exceeded", "timeout"},
		{"connection refused", "dial tcp: connection refused", "network_error"},
		{"no route", "no route to host 10.0.0.1", "network_error"},
		{"unreachable", "network unreachable", "network_error"},
		{"io timeout", "read tcp: i/o timeout", "timeout"},
		{"exit code", "process exit code 1", "script_error"},
		{"non-zero", "non-zero exit status", "script_error"},
		{"failed colon", "remediate phase failed: script error", "script_error"},
		{"not found", "runbook not found", "not_found"},
		{"no such", "no such file or directory", "not_found"},
		{"missing", "missing required parameter", "not_found"},
		{"unknown error", "something unexpected happened", "unknown"},
		{"case insensitive", "CONNECTION REFUSED", "network_error"},
		{"winrm ntlm mismatch", "http response error: 401 - invalid content type", "auth_failure"},
		{"invalid content type alone", "invalid content type", "auth_failure"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := classifyHealError(tt.errMsg)
			if got != tt.expected {
				t.Errorf("classifyHealError(%q) = %q, want %q", tt.errMsg, got, tt.expected)
			}
		})
	}
}
