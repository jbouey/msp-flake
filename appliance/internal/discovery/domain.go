// Package discovery implements AD domain discovery and computer enumeration.
//
// Domain discovery uses multiple methods (DNS SRV, DHCP, resolv.conf, LDAP rootDSE)
// to zero-friction detect the Active Directory domain on the local network.
// AD enumeration queries domain controllers for computer objects.
package discovery

import (
	"context"
	"fmt"
	"log"
	"net"
	"os"
	"os/exec"
	"regexp"
	"strings"
	"time"
)

// DiscoveredDomain holds information about a discovered AD domain.
type DiscoveredDomain struct {
	DomainName        string   `json:"domain_name"`        // e.g., "northvalley.local"
	NetBIOSName       string   `json:"netbios_name"`       // e.g., "NORTHVALLEY"
	DomainControllers []string `json:"domain_controllers"` // DC hostnames/IPs
	DNSServers        []string `json:"dns_servers"`
	DiscoveredAt      string   `json:"discovered_at"`
	DiscoveryMethod   string   `json:"discovery_method"` // dns_srv, dhcp, resolv_conf, ldap_rootdse
}

// DomainDiscovery discovers AD domains on the local network.
type DomainDiscovery struct {
	knownCandidates []string // Known DNS/DC IPs to try
}

// NewDomainDiscovery creates a new domain discoverer.
func NewDomainDiscovery(knownCandidates []string) *DomainDiscovery {
	return &DomainDiscovery{knownCandidates: knownCandidates}
}

// Discover attempts to find the AD domain using multiple methods.
// Returns nil if no domain is found.
func (d *DomainDiscovery) Discover(ctx context.Context, timeout time.Duration) *DiscoveredDomain {
	if timeout == 0 {
		timeout = 30 * time.Second
	}

	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	// Method 1: DNS SRV records
	if domain := d.discoverViaDNSSRV(ctx); domain != nil {
		domain.DiscoveryMethod = "dns_srv"
		log.Printf("[discovery] Domain found via DNS SRV: %s", domain.DomainName)
		return domain
	}

	// Method 2: resolv.conf search domains
	if domain := d.discoverViaResolvConf(ctx); domain != nil {
		domain.DiscoveryMethod = "resolv_conf"
		log.Printf("[discovery] Domain found via resolv.conf: %s", domain.DomainName)
		return domain
	}

	// Method 3: DHCP domain suffix
	if domain := d.discoverViaDHCP(ctx); domain != nil {
		domain.DiscoveryMethod = "dhcp"
		log.Printf("[discovery] Domain found via DHCP: %s", domain.DomainName)
		return domain
	}

	// Method 4: LDAP rootDSE on known hosts
	if domain := d.discoverViaLDAP(ctx); domain != nil {
		domain.DiscoveryMethod = "ldap_rootdse"
		log.Printf("[discovery] Domain found via LDAP rootDSE: %s", domain.DomainName)
		return domain
	}

	log.Printf("[discovery] AD domain auto-discovery failed — manual configuration required")
	return nil
}

// discoverViaDNSSRV queries DNS SRV records for AD domain controllers.
func (d *DomainDiscovery) discoverViaDNSSRV(ctx context.Context) *DiscoveredDomain {
	// Get search domains from resolv.conf
	searchDomains := getDNSSearchDomains()
	if len(searchDomains) == 0 {
		return nil
	}

	for _, domain := range searchDomains {
		srvQuery := fmt.Sprintf("_ldap._tcp.dc._msdcs.%s", domain)

		cmd := exec.CommandContext(ctx, "dig", "+short", "SRV", srvQuery)
		output, err := cmd.Output()
		if err != nil {
			continue
		}

		var dcs []string
		for _, line := range strings.Split(strings.TrimSpace(string(output)), "\n") {
			parts := strings.Fields(line)
			if len(parts) >= 4 {
				dcHost := strings.TrimSuffix(parts[3], ".")
				if dcHost != "" {
					dcs = append(dcs, dcHost)
				}
			}
		}

		if len(dcs) > 0 {
			return &DiscoveredDomain{
				DomainName:        domain,
				NetBIOSName:       extractNetBIOS(domain),
				DomainControllers: dcs,
				DNSServers:        getDNSServers(),
				DiscoveredAt:      time.Now().UTC().Format(time.RFC3339),
			}
		}
	}

	return nil
}

// discoverViaResolvConf checks resolv.conf for domain hints and validates via LDAP.
func (d *DomainDiscovery) discoverViaResolvConf(ctx context.Context) *DiscoveredDomain {
	searchDomains := getDNSSearchDomains()
	dnsServers := getDNSServers()

	for _, domain := range searchDomains {
		// Try each DNS server as a potential DC
		for _, server := range dnsServers {
			dn := queryLDAPRootDSE(ctx, server)
			if dn != "" {
				discoveredDomain := dnToDomain(dn)
				if discoveredDomain != "" {
					return &DiscoveredDomain{
						DomainName:        discoveredDomain,
						NetBIOSName:       extractNetBIOS(discoveredDomain),
						DomainControllers: []string{server},
						DNSServers:        dnsServers,
						DiscoveredAt:      time.Now().UTC().Format(time.RFC3339),
					}
				}
			}
		}

		// If we have a domain but couldn't validate, still return it
		if strings.Contains(domain, ".") && !strings.HasSuffix(domain, ".in-addr.arpa") {
			return &DiscoveredDomain{
				DomainName:        domain,
				NetBIOSName:       extractNetBIOS(domain),
				DomainControllers: nil,
				DNSServers:        dnsServers,
				DiscoveredAt:      time.Now().UTC().Format(time.RFC3339),
			}
		}
	}

	return nil
}

// discoverViaDHCP reads DHCP lease files for domain suffix.
func (d *DomainDiscovery) discoverViaDHCP(ctx context.Context) *DiscoveredDomain {
	// NixOS systemd-networkd leases
	leaseDir := "/run/systemd/netif/leases"
	entries, err := os.ReadDir(leaseDir)
	if err != nil {
		return nil
	}

	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		data, err := os.ReadFile(fmt.Sprintf("%s/%s", leaseDir, entry.Name()))
		if err != nil {
			continue
		}

		for _, line := range strings.Split(string(data), "\n") {
			if strings.HasPrefix(line, "DOMAINNAME=") {
				domain := strings.TrimPrefix(line, "DOMAINNAME=")
				domain = strings.Trim(domain, "\"' \t")
				if domain != "" && strings.Contains(domain, ".") {
					return &DiscoveredDomain{
						DomainName:   domain,
						NetBIOSName:  extractNetBIOS(domain),
						DNSServers:   getDNSServers(),
						DiscoveredAt: time.Now().UTC().Format(time.RFC3339),
					}
				}
			}
		}
	}

	return nil
}

// discoverViaLDAP tries connecting to known hosts on port 389 and querying rootDSE.
func (d *DomainDiscovery) discoverViaLDAP(ctx context.Context) *DiscoveredDomain {
	candidates := d.knownCandidates

	// Also try DNS servers as candidates
	candidates = append(candidates, getDNSServers()...)

	for _, host := range candidates {
		dn := queryLDAPRootDSE(ctx, host)
		if dn == "" {
			continue
		}

		domain := dnToDomain(dn)
		if domain == "" {
			continue
		}

		return &DiscoveredDomain{
			DomainName:        domain,
			NetBIOSName:       extractNetBIOS(domain),
			DomainControllers: []string{host},
			DNSServers:        getDNSServers(),
			DiscoveredAt:      time.Now().UTC().Format(time.RFC3339),
		}
	}

	return nil
}

// queryLDAPRootDSE sends a minimal LDAP search for defaultNamingContext.
// Uses raw BER encoding — no external LDAP library needed.
func queryLDAPRootDSE(ctx context.Context, host string) string {
	dialer := net.Dialer{Timeout: 5 * time.Second}
	conn, err := dialer.DialContext(ctx, "tcp", net.JoinHostPort(host, "389"))
	if err != nil {
		return ""
	}
	defer conn.Close()

	conn.SetDeadline(time.Now().Add(5 * time.Second))

	// Build LDAP SearchRequest for rootDSE
	// MessageID: 1, SearchRequest: baseObject="", scope=baseObject(0),
	// derefAliases=neverDerefAliases(0), sizeLimit=1, timeLimit=5,
	// typesOnly=false, filter=(objectClass=*), attributes=[defaultNamingContext]
	packet := buildRootDSESearchRequest()

	if _, err := conn.Write(packet); err != nil {
		return ""
	}

	// Read response
	buf := make([]byte, 4096)
	n, err := conn.Read(buf)
	if err != nil || n == 0 {
		return ""
	}

	return extractDefaultNamingContext(buf[:n])
}

// buildRootDSESearchRequest constructs a minimal LDAP SearchRequest packet.
func buildRootDSESearchRequest() []byte {
	// Attribute: "defaultNamingContext"
	attr := berOctetString("defaultNamingContext")
	attrList := berSequence(attr)

	// Filter: (objectClass=*)  → present filter, tag 0x87
	filter := []byte{0x87, 0x0b}
	filter = append(filter, []byte("objectClass")...)

	// SearchRequest body
	var body []byte
	body = append(body, berOctetString("")...)       // baseObject
	body = append(body, berEnum(0)...)                // scope: baseObject
	body = append(body, berEnum(0)...)                // derefAliases: neverDerefAliases
	body = append(body, berInteger(1)...)             // sizeLimit
	body = append(body, berInteger(5)...)             // timeLimit
	body = append(body, berBool(false)...)            // typesOnly
	body = append(body, filter...)                    // filter
	body = append(body, attrList...)                  // attributes

	// Wrap in SearchRequest application tag (0x63)
	searchReq := berTagged(0x63, body)

	// Wrap in LDAPMessage sequence
	var msg []byte
	msg = append(msg, berInteger(1)...) // messageID
	msg = append(msg, searchReq...)

	return berSequence(msg)
}

// extractDefaultNamingContext parses an LDAP response for the defaultNamingContext value.
func extractDefaultNamingContext(data []byte) string {
	// Look for "defaultNamingContext" marker
	marker := "defaultNamingContext"
	idx := strings.Index(string(data), marker)
	if idx < 0 {
		// Fallback: regex for DC= pattern
		return extractDCPattern(data)
	}

	// After the marker, look for the next OCTET STRING (tag 0x04)
	rest := data[idx+len(marker):]
	for i := 0; i < len(rest)-2; i++ {
		if rest[i] == 0x04 {
			length := int(rest[i+1])
			if length > 0 && i+2+length <= len(rest) {
				return string(rest[i+2 : i+2+length])
			}
		}
	}

	return extractDCPattern(data)
}

var dcPattern = regexp.MustCompile(`DC=[A-Za-z0-9_-]+(?:,DC=[A-Za-z0-9_-]+)*`)

func extractDCPattern(data []byte) string {
	match := dcPattern.Find(data)
	if match != nil {
		return string(match)
	}
	return ""
}

// --- BER encoding helpers ---

func berSequence(data []byte) []byte {
	return berTagged(0x30, data)
}

func berTagged(tag byte, data []byte) []byte {
	var result []byte
	result = append(result, tag)
	result = append(result, berLength(len(data))...)
	result = append(result, data...)
	return result
}

func berLength(l int) []byte {
	if l < 128 {
		return []byte{byte(l)}
	}
	if l < 256 {
		return []byte{0x81, byte(l)}
	}
	return []byte{0x82, byte(l >> 8), byte(l & 0xff)}
}

func berInteger(val int) []byte {
	var data []byte
	if val == 0 {
		data = []byte{0}
	} else if val < 128 {
		data = []byte{byte(val)}
	} else if val < 32768 {
		data = []byte{byte(val >> 8), byte(val & 0xff)}
	} else {
		data = []byte{byte(val >> 24), byte(val >> 16), byte(val >> 8), byte(val)}
	}
	return append([]byte{0x02, byte(len(data))}, data...)
}

func berOctetString(val string) []byte {
	data := []byte(val)
	var result []byte
	result = append(result, 0x04)
	result = append(result, berLength(len(data))...)
	result = append(result, data...)
	return result
}

func berEnum(val int) []byte {
	return []byte{0x0a, 0x01, byte(val)}
}

func berBool(val bool) []byte {
	if val {
		return []byte{0x01, 0x01, 0xff}
	}
	return []byte{0x01, 0x01, 0x00}
}

// --- Domain helpers ---

// dnToDomain converts "DC=northvalley,DC=local" to "northvalley.local".
func dnToDomain(dn string) string {
	var parts []string
	for _, component := range strings.Split(dn, ",") {
		component = strings.TrimSpace(component)
		upper := strings.ToUpper(component)
		if strings.HasPrefix(upper, "DC=") {
			parts = append(parts, component[3:])
		}
	}
	return strings.Join(parts, ".")
}

// extractNetBIOS extracts the NetBIOS name from a domain.
func extractNetBIOS(domain string) string {
	parts := strings.Split(domain, ".")
	if len(parts) > 0 {
		return strings.ToUpper(parts[0])
	}
	return ""
}

// getDNSSearchDomains reads search domains from /etc/resolv.conf.
func getDNSSearchDomains() []string {
	data, err := os.ReadFile("/etc/resolv.conf")
	if err != nil {
		return nil
	}

	var domains []string
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "search ") {
			domains = append(domains, strings.Fields(line)[1:]...)
		} else if strings.HasPrefix(line, "domain ") {
			fields := strings.Fields(line)
			if len(fields) >= 2 {
				domains = append(domains, fields[1])
			}
		}
	}
	return domains
}

// getDNSServers reads nameservers from /etc/resolv.conf.
func getDNSServers() []string {
	data, err := os.ReadFile("/etc/resolv.conf")
	if err != nil {
		return nil
	}

	var servers []string
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "nameserver ") {
			fields := strings.Fields(line)
			if len(fields) >= 2 {
				servers = append(servers, fields[1])
			}
		}
	}
	return servers
}
