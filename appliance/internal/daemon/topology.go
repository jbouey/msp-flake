package daemon

import (
	"fmt"
	"net"
)

// SubnetGroup groups discovered devices by their /24 subnet
type SubnetGroup struct {
	Subnet  string             // e.g. "192.168.88.0/24"
	Gateway string             // first IP seen (heuristic)
	Devices []discoveredDevice
}

// groupBySubnet organizes devices into subnet groups based on /24 boundaries
func groupBySubnet(devices []discoveredDevice) []SubnetGroup {
	groups := make(map[string]*SubnetGroup)

	for _, d := range devices {
		ip := net.ParseIP(d.IPAddress)
		if ip == nil {
			continue
		}
		ip4 := ip.To4()
		if ip4 == nil {
			continue
		}

		// /24 subnet key
		subnetKey := fmt.Sprintf("%d.%d.%d.0/24", ip4[0], ip4[1], ip4[2])

		group, exists := groups[subnetKey]
		if !exists {
			group = &SubnetGroup{Subnet: subnetKey}
			groups[subnetKey] = group
		}
		group.Devices = append(group.Devices, d)
	}

	// Convert map to slice
	result := make([]SubnetGroup, 0, len(groups))
	for _, g := range groups {
		result = append(result, *g)
	}
	return result
}

// detectUnexpectedSubnets identifies devices on subnets different from the primary
// (the subnet with the most devices). Returns devices on unexpected subnets.
func detectUnexpectedSubnets(groups []SubnetGroup) []discoveredDevice {
	if len(groups) <= 1 {
		return nil // single subnet, nothing unexpected
	}

	// Find primary subnet (most devices)
	primary := groups[0]
	for _, g := range groups[1:] {
		if len(g.Devices) > len(primary.Devices) {
			primary = g
		}
	}

	// Devices on non-primary subnets are unexpected
	var unexpected []discoveredDevice
	for _, g := range groups {
		if g.Subnet == primary.Subnet {
			continue
		}
		unexpected = append(unexpected, g.Devices...)
	}
	return unexpected
}

// getDeviceSubnet returns the /24 subnet string for an IP
func getDeviceSubnet(ipStr string) string {
	ip := net.ParseIP(ipStr)
	if ip == nil {
		return ""
	}
	ip4 := ip.To4()
	if ip4 == nil {
		return ""
	}
	return fmt.Sprintf("%d.%d.%d.0/24", ip4[0], ip4[1], ip4[2])
}
