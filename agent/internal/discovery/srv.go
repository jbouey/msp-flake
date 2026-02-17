// Package discovery provides DNS SRV-based appliance auto-discovery.
// Go agents find the appliance by looking up _osiris-grpc._tcp.<domain>.
package discovery

import (
	"fmt"
	"log"
	"net"
	"strings"
	"time"
)

const (
	// SRVService is the service name in the SRV record.
	SRVService = "osiris-grpc"
	// SRVProto is the protocol in the SRV record.
	SRVProto = "tcp"
	// MaxRetries is the default number of SRV lookup retries.
	MaxRetries = 3
	// RetryDelay is the base delay between retries.
	RetryDelay = 5 * time.Second
)

// DiscoverAppliance looks up the appliance address via DNS SRV records.
// Returns "host:port" or error if not found.
func DiscoverAppliance(domain string) (string, error) {
	_, addrs, err := net.LookupSRV(SRVService, SRVProto, domain)
	if err != nil {
		return "", fmt.Errorf("SRV lookup for _osiris-grpc._tcp.%s failed: %w", domain, err)
	}

	if len(addrs) == 0 {
		return "", fmt.Errorf("no SRV records found for _osiris-grpc._tcp.%s", domain)
	}

	// Use highest priority (lowest Priority number)
	best := addrs[0]
	target := strings.TrimSuffix(best.Target, ".")

	return fmt.Sprintf("%s:%d", target, best.Port), nil
}

// DiscoverApplianceWithRetry retries SRV discovery with linear backoff.
func DiscoverApplianceWithRetry(domain string, maxRetries int) (string, error) {
	var lastErr error
	for i := 0; i < maxRetries; i++ {
		addr, err := DiscoverAppliance(domain)
		if err == nil {
			return addr, nil
		}
		lastErr = err
		if i < maxRetries-1 {
			delay := RetryDelay * time.Duration(i+1)
			log.Printf("[discovery] SRV lookup attempt %d/%d failed: %v, retrying in %v", i+1, maxRetries, err, delay)
			time.Sleep(delay)
		}
	}
	return "", fmt.Errorf("SRV discovery failed after %d attempts: %w", maxRetries, lastErr)
}
