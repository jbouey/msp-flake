package daemon

import (
	"testing"
)

func TestParseSSHBanner(t *testing.T) {
	tests := []struct {
		name       string
		banner     string
		wantOS     string
		wantDistro string
	}{
		{
			name:       "Ubuntu",
			banner:     "SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.6",
			wantOS:     "linux",
			wantDistro: "ubuntu",
		},
		{
			name:       "Debian",
			banner:     "SSH-2.0-OpenSSH_9.2p1 Debian-2+deb12u2",
			wantOS:     "linux",
			wantDistro: "debian",
		},
		{
			name:       "macOS Apple",
			banner:     "SSH-2.0-OpenSSH_9.0 Apple_SSH_2.0.1",
			wantOS:     "macos",
			wantDistro: "",
		},
		{
			name:       "RHEL",
			banner:     "SSH-2.0-OpenSSH_8.7 Red Hat-8.7p1",
			wantOS:     "linux",
			wantDistro: "rhel",
		},
		{
			name:       "CentOS",
			banner:     "SSH-2.0-OpenSSH_7.4 CentOS-7.4p1",
			wantOS:     "linux",
			wantDistro: "centos",
		},
		{
			name:       "Generic Linux",
			banner:     "SSH-2.0-OpenSSH_8.4",
			wantOS:     "linux",
			wantDistro: "",
		},
		{
			name:       "Windows OpenSSH",
			banner:     "SSH-2.0-OpenSSH_for_Windows_8.1",
			wantOS:     "windows",
			wantDistro: "",
		},
		{
			name:       "Empty banner",
			banner:     "",
			wantOS:     "unknown",
			wantDistro: "",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			gotOS, gotDistro := parseSSHBanner(tt.banner)
			if gotOS != tt.wantOS {
				t.Errorf("parseSSHBanner(%q) osType = %q, want %q", tt.banner, gotOS, tt.wantOS)
			}
			if gotDistro != tt.wantDistro {
				t.Errorf("parseSSHBanner(%q) distro = %q, want %q", tt.banner, gotDistro, tt.wantDistro)
			}
		})
	}
}

func TestClassifyFromProbes(t *testing.T) {
	tests := []struct {
		name       string
		result     ProbeResult
		wantOS     string
		wantDistro string
	}{
		{
			name: "SSH Ubuntu banner",
			result: ProbeResult{
				SSHOpen:   true,
				SSHBanner: "SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.6",
			},
			wantOS:     "linux",
			wantDistro: "ubuntu",
		},
		{
			name: "WinRM only",
			result: ProbeResult{
				WinRMOpen: true,
			},
			wantOS:     "windows",
			wantDistro: "",
		},
		{
			name: "SSH open no banner",
			result: ProbeResult{
				SSHOpen: true,
			},
			wantOS:     "linux",
			wantDistro: "",
		},
		{
			name:       "Nothing open",
			result:     ProbeResult{},
			wantOS:     "unknown",
			wantDistro: "",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			classifyFromProbes(&tt.result)
			if tt.result.OSType != tt.wantOS {
				t.Errorf("classifyFromProbes() OSType = %q, want %q", tt.result.OSType, tt.wantOS)
			}
			if tt.result.Distro != tt.wantDistro {
				t.Errorf("classifyFromProbes() Distro = %q, want %q", tt.result.Distro, tt.wantDistro)
			}
		})
	}
}
