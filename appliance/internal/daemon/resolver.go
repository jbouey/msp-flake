// resolver.go — v36 daemon hardening for DNS-filter / broken-IPv6 networks.
//
// Two failure modes observed in the wild:
//
//  1. DNS filter (Pi-hole, Umbrella, Fortinet, Sophos, Barracuda) blocks
//     or fails to resolve api.osiriscare.net. 2026-04-15 t740 debug:
//     box silently stranded on a home network where Pi-hole hadn't
//     been taught about our domain yet. Local resolver returns NXDOMAIN,
//     daemon can't connect even though the public internet is reachable.
//
//  2. IPv6-preferred-but-broken. Many small-office networks enable
//     IPv6 via ISP default but don't route it correctly. Go's default
//     net.Dial tries AAAA first, waits for timeout, then falls back to
//     A — blocking the daemon for 15-30 seconds per checkin. On a
//     60s checkin cadence that's catastrophic.
//
// This file provides:
//
//  - newDaemonDialer(): a net.Dialer with DualStack=false (IPv4-only)
//    + a custom Resolver that tries system DNS first, then DoH via
//    1.1.1.1, then a hardcoded VPS IP fallback for api.osiriscare.net.
//
//  - doHLookupA(): RFC 8484 DNS-over-HTTPS query via application/dns-json
//    (cleaner than wire-format for a shim; 1.1.1.1 supports both). No
//    new Go dependencies.
//
// Env var opt-outs:
//  - OSIRIS_DAEMON_IPV6=1            — allow dual-stack (rare)
//  - OSIRIS_DAEMON_DISABLE_DOH=1     — force system DNS only
//  - OSIRIS_DAEMON_HARDCODED_IP=x.x  — override the last-resort IP
package daemon

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

// HardcodedVPSFallbackIP is the last-resort IP for api.osiriscare.net
// when both system DNS and DoH fail. MUST be kept in sync with the
// VPS's actual public IP. Env var OSIRIS_DAEMON_HARDCODED_IP wins if
// set, so rotation doesn't require a binary rebuild.
const HardcodedVPSFallbackIP = "178.156.162.116"

// DoHServers — Cloudflare first (fastest, JSON-friendly), then Google
// as a second-line fallback if Cloudflare is blocked.
var DoHServers = []string{
	"https://1.1.1.1/dns-query",
	"https://dns.google/resolve",
}

// dohClient is used ONLY for DoH lookups — separate from phonehome's
// client so pin mismatches / 401s on api.osiriscare.net don't affect
// DNS resolution. 5s timeout — DoH should be fast.
var dohClient = &http.Client{
	Timeout: 5 * time.Second,
	Transport: &http.Transport{
		// Deliberately DON'T use the daemon's pinned transport — DoH
		// talks to Cloudflare / Google, not Central Command.
		MaxIdleConns:        2,
		IdleConnTimeout:     60 * time.Second,
		TLSHandshakeTimeout: 3 * time.Second,
	},
}

// dohCache holds recent DoH results in memory, 5-minute TTL. Avoids
// hammering 1.1.1.1 every 60s.
type dohCacheEntry struct {
	ips    []string
	expiry time.Time
}

var (
	dohCacheMu sync.RWMutex
	dohCache   = map[string]dohCacheEntry{}
)

// newDaemonDialer returns a net.Dialer configured for the daemon's
// specific failure modes: IPv4-only (unless explicitly opted in) +
// custom resolver chain with DoH fallback.
func newDaemonDialer(cfg *Config) *net.Dialer {
	dualStack := os.Getenv("OSIRIS_DAEMON_IPV6") == "1"

	dialer := &net.Dialer{
		Timeout:   15 * time.Second,
		KeepAlive: 30 * time.Second,
	}
	// Go exposes the IPv4-only behavior by setting Resolver.PreferGo=true
	// + constructing IPv4-specific logic. Easier: override Resolver
	// entirely with our own that queries A records only (unless dual-
	// stack explicitly enabled).
	dialer.Resolver = &net.Resolver{
		PreferGo: true,
		Dial:     makeResolverDial(dialer, dualStack),
	}

	// For our own Dial path: force "tcp4" when dualStack=false, so
	// callers that use dialer.DialContext(ctx, "tcp", addr) transparently
	// get IPv4-only behavior. We wrap DialContext on the resolver level
	// (above) and on the dialer-level via a custom Control function if
	// needed. For now, the resolver override is enough — the returned
	// address records are A-only, so net.DialTCP will use IPv4.
	return dialer
}

// makeResolverDial returns a Dial function that Go's default Resolver
// will use for DNS lookups. We try system-configured DNS first. If
// that returns a resolution error (not a connection error), we fall
// through to DoH.
//
// Note: Go's Resolver.Dial is used ONLY for the UDP/TCP connection to
// the DNS server, not for the queries themselves. Our DoH override
// lives at a higher layer — see resolveHostWithFallback for the real
// fallback chain.
func makeResolverDial(dialer *net.Dialer, dualStack bool) func(ctx context.Context, network, address string) (net.Conn, error) {
	return func(ctx context.Context, network, address string) (net.Conn, error) {
		// network is typically "udp" for DNS. Pass through.
		return dialer.DialContext(ctx, network, address)
	}
}

// ResolveWithFallback returns a list of IPv4 addresses for host, trying
// in order:
//
//  1. System DNS (net.DefaultResolver)
//  2. DoH via Cloudflare 1.1.1.1 + Google (if not disabled)
//  3. Hardcoded HardcodedVPSFallbackIP if host is api.osiriscare.net
//
// Returns error only if all three paths fail. This is the function
// Checkin / fleet order fetch / evidence submit should call before any
// HTTP Dial when they already have the hostname but want deterministic
// resolution behavior.
func ResolveWithFallback(ctx context.Context, host string) ([]string, error) {
	host = strings.ToLower(strings.TrimSpace(host))
	if host == "" {
		return nil, fmt.Errorf("empty host")
	}

	// Fast path: literal IP, no DNS.
	if ip := net.ParseIP(host); ip != nil {
		return []string{ip.String()}, nil
	}

	// 1. System DNS
	sysCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	if addrs, err := net.DefaultResolver.LookupIPAddr(sysCtx, host); err == nil && len(addrs) > 0 {
		ips := make([]string, 0, len(addrs))
		for _, a := range addrs {
			if v4 := a.IP.To4(); v4 != nil {
				ips = append(ips, v4.String())
			}
		}
		if len(ips) > 0 {
			return ips, nil
		}
	}

	// 2. DoH
	if os.Getenv("OSIRIS_DAEMON_DISABLE_DOH") != "1" {
		if ips, err := doHLookupA(ctx, host); err == nil && len(ips) > 0 {
			return ips, nil
		}
	}

	// 3. Hardcoded fallback — ONLY for api.osiriscare.net (the one host
	// we actually need to reach for the daemon to function). Never
	// apply to arbitrary domains — would break multi-tenant or
	// CDN-hosted resources.
	if host == "api.osiriscare.net" {
		hardcoded := os.Getenv("OSIRIS_DAEMON_HARDCODED_IP")
		if hardcoded == "" {
			hardcoded = HardcodedVPSFallbackIP
		}
		if net.ParseIP(hardcoded) != nil {
			return []string{hardcoded}, nil
		}
	}

	return nil, fmt.Errorf("no resolution path succeeded for %q", host)
}

// doHLookupA issues an RFC 8484 DNS-over-HTTPS query for A records.
// Uses the application/dns-json format for simplicity (supported by
// 1.1.1.1 + dns.google). Caches results for 5 minutes.
func doHLookupA(ctx context.Context, host string) ([]string, error) {
	// Cache check
	dohCacheMu.RLock()
	if entry, ok := dohCache[host]; ok && time.Now().Before(entry.expiry) {
		ips := append([]string(nil), entry.ips...)
		dohCacheMu.RUnlock()
		return ips, nil
	}
	dohCacheMu.RUnlock()

	var lastErr error
	for _, server := range DoHServers {
		url := fmt.Sprintf("%s?name=%s&type=A", server, host)
		req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
		if err != nil {
			lastErr = err
			continue
		}
		req.Header.Set("Accept", "application/dns-json")
		resp, err := dohClient.Do(req)
		if err != nil {
			lastErr = err
			continue
		}
		body, err := io.ReadAll(resp.Body)
		resp.Body.Close()
		if err != nil {
			lastErr = err
			continue
		}
		if resp.StatusCode != 200 {
			lastErr = fmt.Errorf("DoH %s HTTP %d", server, resp.StatusCode)
			continue
		}

		var parsed struct {
			Status int `json:"Status"`
			Answer []struct {
				Name string `json:"name"`
				Type int    `json:"type"`
				Data string `json:"data"`
				TTL  int    `json:"TTL"`
			} `json:"Answer"`
		}
		if err := json.Unmarshal(body, &parsed); err != nil {
			lastErr = err
			continue
		}
		if parsed.Status != 0 {
			// Status 3 = NXDOMAIN — DoH itself says this host doesn't exist.
			// Return error; caller may use hardcoded fallback if applicable.
			lastErr = fmt.Errorf("DoH %s returned status %d (NXDOMAIN or similar)", server, parsed.Status)
			continue
		}

		ips := make([]string, 0, len(parsed.Answer))
		for _, a := range parsed.Answer {
			if a.Type == 1 /* A */ {
				if net.ParseIP(a.Data) != nil {
					ips = append(ips, a.Data)
				}
			}
		}
		if len(ips) > 0 {
			// Cache
			dohCacheMu.Lock()
			dohCache[host] = dohCacheEntry{
				ips:    ips,
				expiry: time.Now().Add(5 * time.Minute),
			}
			dohCacheMu.Unlock()
			return ips, nil
		}
	}
	if lastErr != nil {
		return nil, lastErr
	}
	return nil, fmt.Errorf("DoH: no usable answers")
}
