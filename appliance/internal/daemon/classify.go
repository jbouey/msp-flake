package daemon

// classifyADJoined determines if a device is likely AD-joined (via SSSD/realmd/Winbind)
// based on probe results and AD enumeration data.
//
// Detection methods:
//  1. AD cross-reference: hostname/IP found in the AD computer object cache.
//  2. Kerberos probe: port 88 open on a Linux host suggests a Kerberos client
//     (SSSD/realmd joins configure the system as a Kerberos principal).
//
// Windows hosts with Kerberos open are explicitly excluded — every Windows domain
// member listens on 88 as part of normal domain membership, so it is not a
// distinguishing signal for SSSD-style Linux joins.
func classifyADJoined(probe ProbeResult, adHostnames map[string]bool) bool {
	// Method 1: device IP or hostname found in AD computer objects (strongest signal)
	if len(adHostnames) > 0 {
		if adHostnames[probe.IP] {
			return true
		}
	}

	// Method 2: Kerberos port open on a Linux host = likely domain-joined via SSSD/realmd
	if probe.KerberosOpen && probe.OSType == "linux" {
		return true
	}

	return false
}
