package discovery

import (
	"context"
	"fmt"
	"log"
	"net"
	"strings"
	"time"
)

// DiscoverApplianceMDNS resolves the appliance via mDNS/DNS-SD.
// Uses the system resolver which handles .local via multicast DNS on:
//   - macOS: native mDNS (Bonjour)
//   - Linux: nss-mdns + Avahi
//   - Windows: native mDNS (Win10+)
//
// Returns "host:port" or error if not found within timeout.
func DiscoverApplianceMDNS(ctx context.Context, timeout time.Duration) (string, error) {
	if timeout == 0 {
		timeout = 3 * time.Second
	}

	resolveCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	resolver := &net.Resolver{}

	// Look up SRV record for _osiris-grpc._tcp.local
	// The system resolver dispatches .local queries to mDNS automatically
	_, addrs, err := resolver.LookupSRV(resolveCtx, SRVService, SRVProto, "local")
	if err != nil {
		return "", fmt.Errorf("mDNS SRV lookup for _%s._%s.local failed: %w", SRVService, SRVProto, err)
	}

	if len(addrs) == 0 {
		return "", fmt.Errorf("no mDNS SRV records found for _%s._%s.local", SRVService, SRVProto)
	}

	// Use highest priority (lowest Priority number)
	best := addrs[0]
	target := strings.TrimSuffix(best.Target, ".")

	// Resolve the .local hostname to an IP (needed for gRPC dial)
	ips, err := resolver.LookupHost(resolveCtx, target)
	if err != nil || len(ips) == 0 {
		// Fall back to using the hostname directly
		return fmt.Sprintf("%s:%d", target, best.Port), nil
	}

	// Prefer IPv4
	ip := ips[0]
	for _, candidate := range ips {
		if !strings.Contains(candidate, ":") {
			ip = candidate
			break
		}
	}

	return fmt.Sprintf("%s:%d", ip, best.Port), nil
}

// DiscoverApplianceMDNSWithRetry tries mDNS discovery with retries.
// Returns quickly on first success. Designed for reconnect scenarios
// where the appliance IP may have changed via DHCP.
func DiscoverApplianceMDNSWithRetry(ctx context.Context, maxRetries int) (string, error) {
	if maxRetries < 1 {
		maxRetries = 1
	} else if maxRetries > 5 {
		maxRetries = 5
	}

	var lastErr error
	for i := 0; i < maxRetries; i++ {
		addr, err := DiscoverApplianceMDNS(ctx, 3*time.Second)
		if err == nil {
			return addr, nil
		}
		lastErr = err
		if i < maxRetries-1 {
			delay := time.Duration(i+1) * 2 * time.Second
			log.Printf("[discovery] mDNS attempt %d/%d failed: %v, retrying in %v", i+1, maxRetries, err, delay)
			select {
			case <-ctx.Done():
				return "", ctx.Err()
			case <-time.After(delay):
			}
		}
	}
	return "", fmt.Errorf("mDNS discovery failed after %d attempts: %w", maxRetries, lastErr)
}

// LinkLocalAddr is the deterministic secondary IP assigned to all OsirisCare appliances.
// Used as a last-resort fallback when mDNS is blocked and DHCP drifts.
const LinkLocalAddr = "169.254.88.1"

// DiscoverApplianceLinkLocal probes the well-known link-local address.
// Returns "host:port" if the gRPC port is reachable, error otherwise.
func DiscoverApplianceLinkLocal(timeout time.Duration) (string, error) {
	if timeout == 0 {
		timeout = 2 * time.Second
	}
	addr := fmt.Sprintf("%s:%d", LinkLocalAddr, 50051)
	conn, err := net.DialTimeout("tcp", addr, timeout)
	if err != nil {
		return "", fmt.Errorf("link-local probe %s failed: %w", addr, err)
	}
	conn.Close()
	return addr, nil
}
