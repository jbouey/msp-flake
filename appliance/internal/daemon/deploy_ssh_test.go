package daemon

import (
	"strings"
	"testing"
)

func TestBuildInstallScript_Linux(t *testing.T) {
	script := buildInstallScript("linux", "/tmp/osiris-agent", "https://api.osiriscare.net", "site-abc-123")
	if !strings.Contains(script, "systemctl") {
		t.Error("Linux install script should use systemctl")
	}
	if !strings.Contains(script, "/opt/osiriscare") {
		t.Error("Linux install path should be /opt/osiriscare")
	}
	if !strings.Contains(script, "site-abc-123") {
		t.Error("Install script should contain site_id")
	}
}

func TestBuildInstallScript_MacOS(t *testing.T) {
	script := buildInstallScript("macos", "/tmp/osiris-agent", "https://api.osiriscare.net", "site-abc-123")
	if !strings.Contains(script, "launchctl") {
		t.Error("macOS install script should use launchctl")
	}
	if !strings.Contains(script, "/Library/OsirisCare") {
		t.Error("macOS install path should be /Library/OsirisCare")
	}
}

func TestBuildInstallScript_Unknown(t *testing.T) {
	script := buildInstallScript("freebsd", "/tmp/osiris-agent", "https://api.osiriscare.net", "site-abc-123")
	if !strings.Contains(script, "DEPLOY_UNSUPPORTED_OS") {
		t.Error("Unknown OS should return unsupported error")
	}
}

func TestBuildInstallScript_LinuxConfigJSON(t *testing.T) {
	script := buildInstallScript("linux", "/tmp/osiris-agent", "https://api.osiriscare.net", "site-xyz")
	if !strings.Contains(script, `"api_url"`) {
		t.Error("Install script should write config JSON with api_url")
	}
	if !strings.Contains(script, `"site_id"`) {
		t.Error("Install script should write config JSON with site_id")
	}
	if !strings.Contains(script, "/var/lib/osiriscare") {
		t.Error("Linux data_dir should be /var/lib/osiriscare")
	}
}

func TestBuildInstallScript_MacOSConfigJSON(t *testing.T) {
	script := buildInstallScript("macos", "/tmp/osiris-agent", "https://api.osiriscare.net", "site-xyz")
	if !strings.Contains(script, "Application Support/OsirisCare") {
		t.Error("macOS data_dir should be under Application Support/OsirisCare")
	}
}

func TestGetLocalBinaryPath_Linux(t *testing.T) {
	d := &Daemon{
		config: &Config{StateDir: "/var/lib/msp"},
	}
	path, err := d.getLocalBinaryPath("linux")
	// File won't exist in test, but path should be correct
	if err == nil {
		t.Error("Expected error for non-existent binary")
	}
	if !strings.Contains(path, "osiris-agent-linux-amd64") {
		t.Errorf("Linux binary path should contain osiris-agent-linux-amd64, got: %s", path)
	}
}

func TestGetLocalBinaryPath_MacOS(t *testing.T) {
	d := &Daemon{
		config: &Config{StateDir: "/var/lib/msp"},
	}
	path, err := d.getLocalBinaryPath("macos")
	// File won't exist in test, but path should be correct
	if err == nil {
		t.Error("Expected error for non-existent binary")
	}
	if !strings.Contains(path, "osiris-agent-darwin-amd64") {
		t.Errorf("macOS binary path should contain osiris-agent-darwin-amd64, got: %s", path)
	}
}

func TestGetLocalBinaryPath_Unknown(t *testing.T) {
	d := &Daemon{
		config: &Config{StateDir: "/var/lib/msp"},
	}
	_, err := d.getLocalBinaryPath("freebsd")
	if err == nil {
		t.Error("Unknown OS type should return an error")
	}
	if !strings.Contains(err.Error(), "unsupported") {
		t.Errorf("Error should mention unsupported OS, got: %v", err)
	}
}
