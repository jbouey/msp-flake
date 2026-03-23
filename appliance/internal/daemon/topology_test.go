package daemon

import "testing"

func TestGroupBySubnet_SingleSubnet(t *testing.T) {
	devices := []discoveredDevice{
		{IPAddress: "192.168.88.1"},
		{IPAddress: "192.168.88.50"},
		{IPAddress: "192.168.88.241"},
	}
	groups := groupBySubnet(devices)
	if len(groups) != 1 {
		t.Errorf("expected 1 subnet, got %d", len(groups))
	}
	if groups[0].Subnet != "192.168.88.0/24" {
		t.Errorf("expected 192.168.88.0/24, got %s", groups[0].Subnet)
	}
	if len(groups[0].Devices) != 3 {
		t.Errorf("expected 3 devices, got %d", len(groups[0].Devices))
	}
}

func TestGroupBySubnet_MultipleSubnets(t *testing.T) {
	devices := []discoveredDevice{
		{IPAddress: "192.168.88.1"},
		{IPAddress: "192.168.88.50"},
		{IPAddress: "10.0.0.5"},
		{IPAddress: "10.0.0.10"},
	}
	groups := groupBySubnet(devices)
	if len(groups) != 2 {
		t.Errorf("expected 2 subnets, got %d", len(groups))
	}
}

func TestDetectUnexpectedSubnets_SingleSubnet(t *testing.T) {
	groups := []SubnetGroup{
		{Subnet: "192.168.88.0/24", Devices: make([]discoveredDevice, 5)},
	}
	unexpected := detectUnexpectedSubnets(groups)
	if len(unexpected) != 0 {
		t.Errorf("single subnet should have no unexpected devices, got %d", len(unexpected))
	}
}

func TestDetectUnexpectedSubnets_MultipleSubnets(t *testing.T) {
	groups := []SubnetGroup{
		{Subnet: "192.168.88.0/24", Devices: make([]discoveredDevice, 10)}, // primary
		{Subnet: "10.0.0.0/24", Devices: []discoveredDevice{{IPAddress: "10.0.0.5"}}},
	}
	unexpected := detectUnexpectedSubnets(groups)
	if len(unexpected) != 1 {
		t.Errorf("expected 1 unexpected device, got %d", len(unexpected))
	}
}

func TestGetDeviceSubnet(t *testing.T) {
	tests := []struct {
		ip   string
		want string
	}{
		{"192.168.88.50", "192.168.88.0/24"},
		{"10.0.0.1", "10.0.0.0/24"},
		{"172.16.5.100", "172.16.5.0/24"},
		{"invalid", ""},
	}
	for _, tt := range tests {
		got := getDeviceSubnet(tt.ip)
		if got != tt.want {
			t.Errorf("getDeviceSubnet(%q) = %q, want %q", tt.ip, got, tt.want)
		}
	}
}
