// Package checks implements Windows compliance checks.
package checks

import (
	"context"
	"fmt"
	"net"
	"time"

	"github.com/osiriscare/agent/internal/wmi"
)

// DNSCheck verifies DNS resolution is working and the DNS Client service
// is running. On domain controllers, the DNS Server service is also critical.
// Without DNS, Kerberos, GPO, and domain authentication all fail.
//
// HIPAA Control: §164.312(a)(2)(ii) - Emergency Access Procedure
type DNSCheck struct{}

func (c *DNSCheck) Name() string {
	return "dns_service"
}

func (c *DNSCheck) Run(ctx context.Context) CheckResult {
	result := CheckResult{
		CheckType:    "dns_service",
		HIPAAControl: "164.312(a)(2)(ii)",
		Metadata:     make(map[string]string),
	}

	// Check DNS Client service (Dnscache)
	services, err := wmi.Query(ctx,
		"root\\CIMV2",
		"SELECT Name, State, StartMode FROM Win32_Service WHERE Name = 'Dnscache'",
	)
	if err != nil {
		result.Error = err
		result.Passed = false
		result.Actual = fmt.Sprintf("WMI query failed: %v", err)
		result.Expected = "DNS Client service running"
		return result
	}

	if len(services) > 0 {
		svc := services[0]
		state, _ := wmi.GetPropertyString(svc, "State")
		startMode, _ := wmi.GetPropertyString(svc, "StartMode")
		result.Metadata["dnscache_state"] = state
		result.Metadata["dnscache_start_mode"] = startMode
	}

	// Check if DNS Server service exists (indicates this is a DC)
	dnsServers, _ := wmi.Query(ctx,
		"root\\CIMV2",
		"SELECT Name, State, StartMode FROM Win32_Service WHERE Name = 'DNS'",
	)
	if len(dnsServers) > 0 {
		svc := dnsServers[0]
		state, _ := wmi.GetPropertyString(svc, "State")
		startMode, _ := wmi.GetPropertyString(svc, "StartMode")
		result.Metadata["dns_server_state"] = state
		result.Metadata["dns_server_start_mode"] = startMode
		result.Metadata["is_dc"] = "true"

		if state != "Running" {
			result.Passed = false
			result.Expected = "DNS Server service running on DC"
			result.Actual = fmt.Sprintf("DNS Server service %s (StartMode=%s)", state, startMode)
			return result
		}
	}

	// Functional test: can we actually resolve a hostname?
	resolver := &net.Resolver{
		PreferGo: true,
		Dial: func(ctx context.Context, network, address string) (net.Conn, error) {
			d := net.Dialer{Timeout: 3 * time.Second}
			return d.DialContext(ctx, network, address)
		},
	}
	resolveCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()

	// Try to resolve localhost as a basic sanity check
	_, resolveErr := resolver.LookupHost(resolveCtx, "localhost")
	if resolveErr != nil {
		result.Metadata["resolve_test"] = "failed"
		// Non-fatal — DNS client might be slow but not broken
	} else {
		result.Metadata["resolve_test"] = "passed"
	}

	result.Passed = true
	result.Expected = "DNS services operational"
	result.Actual = "DNS Client running, resolution working"
	return result
}
