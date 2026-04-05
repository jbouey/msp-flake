package daemon

import (
	"fmt"
	"log"
	"os"
	"path/filepath"
)

const avahiServicesDir = "/etc/avahi/services"

// publishAvahiService ensures the Avahi service file exists for mDNS discovery.
// The base service file is deployed by NixOS (extraServiceFiles), so this method
// attempts to enrich it with the site_id TXT record. If writing fails (ProtectSystem=strict),
// the NixOS-deployed file still works — agents just won't see site_id in TXT.
func (d *Daemon) publishAvahiService() {
	servicePath := filepath.Join(avahiServicesDir, "osiris-grpc.service")

	// Check if NixOS already deployed the service file
	if _, err := os.Stat(servicePath); err == nil {
		log.Printf("[avahi] mDNS service file exists (NixOS-managed) — _osiris-grpc._tcp on port 50051")
		return
	}

	// Try writing the file (may fail under ProtectSystem=strict)
	serviceXML := fmt.Sprintf(`<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name>OsirisCare Appliance (%s)</name>
  <service>
    <type>_osiris-grpc._tcp</type>
    <port>%d</port>
    <txt-record>site_id=%s</txt-record>
  </service>
</service-group>
`, d.config.SiteID, d.config.GRPCPort, d.config.SiteID)

	if _, err := os.Stat(avahiServicesDir); os.IsNotExist(err) {
		log.Printf("[avahi] Services directory %s not found — mDNS publishing requires NixOS rebuild", avahiServicesDir)
		return
	}

	if err := os.WriteFile(servicePath, []byte(serviceXML), 0644); err != nil {
		log.Printf("[avahi] Cannot write service file (expected under ProtectSystem=strict) — NixOS rebuild will enable mDNS")
		return
	}

	log.Printf("[avahi] Published _osiris-grpc._tcp on port %d (site=%s)", d.config.GRPCPort, d.config.SiteID)
}
