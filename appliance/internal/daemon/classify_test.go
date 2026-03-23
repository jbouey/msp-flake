package daemon

import (
	"context"
	"testing"
)

func TestClassifyADJoined_InADList(t *testing.T) {
	probe := ProbeResult{IP: "192.168.88.239", OSType: "linux"}
	adHosts := map[string]bool{"192.168.88.239": true}
	if !classifyADJoined(probe, adHosts) {
		t.Error("should be AD-joined when in AD host list")
	}
}

func TestClassifyADJoined_KerberosLinux(t *testing.T) {
	probe := ProbeResult{IP: "192.168.88.239", OSType: "linux", KerberosOpen: true}
	if !classifyADJoined(probe, nil) {
		t.Error("Linux with Kerberos port open should be detected as AD-joined")
	}
}

func TestClassifyADJoined_KerberosWindows(t *testing.T) {
	probe := ProbeResult{IP: "192.168.88.250", OSType: "windows", KerberosOpen: true}
	// Windows Kerberos is normal — doesn't mean SSSD-joined
	if classifyADJoined(probe, nil) {
		t.Error("Windows with Kerberos should NOT trigger SSSD detection")
	}
}

func TestClassifyADJoined_NoSignals(t *testing.T) {
	probe := ProbeResult{IP: "192.168.88.239", OSType: "linux"}
	if classifyADJoined(probe, nil) {
		t.Error("no signals should not be AD-joined")
	}
}

func TestCheckPort_Helper(t *testing.T) {
	// Verify it doesn't panic on unreachable address
	// TEST-NET (192.0.2.0/24) is guaranteed unreachable per RFC 5737
	result := checkPort(context.Background(), "192.0.2.1", 88)
	if result {
		t.Error("unreachable IP should return false")
	}
}
