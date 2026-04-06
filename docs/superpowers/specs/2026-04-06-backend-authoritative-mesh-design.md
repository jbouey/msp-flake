# Backend-Authoritative Mesh (Hybrid C+)

**Date:** 2026-04-06
**Status:** Draft
**Scope:** Multi-appliance target assignment, evidence verification, mesh coordination

## Problem

With 3 appliances at north-valley-branch-2 across 2 subnets (88.x and 0.x), the current client-side mesh has three failures:

1. **Asymmetric routing:** The 0.x appliance (T640) can be probed by 88.x peers but can't reach them back. It reports 0 peers, ring size 1 — effectively isolated.
2. **Evidence key collision:** Single `sites.agent_public_key` means the last appliance to checkin overwrites the registered key. Other appliances get evidence rejections.
3. **Identity confusion:** All appliances register as "osiriscare" — indistinguishable in the UI.

Issues 2 and 3 are already fixed in this session (migrations 125-126). This spec addresses the mesh coordination architecture.

## Current Architecture

```
Daemon (mesh.go):
  - ARP scan → discover same-subnet peers → gRPC probe → add to hash ring
  - Backend delivers sibling IPs/MACs → gRPC probe → add to ring (cross-subnet)
  - Hash ring determines target ownership: OwnsTarget(selfMAC, targetIP)
  - Ring is the SOLE authority — no backend confirmation

Problem: Cross-subnet probes fail one-way → ring diverges → target gaps/overlaps
```

## Design: Backend-Authoritative Target Assignment

### Core Principle

The backend is the **authority** for which appliance scans which targets. The daemon's local hash ring becomes a **cache** of the backend's assignment, not the source of truth.

### Architecture

```
Central Command (authority):
  Checkin response now includes:
    "target_assignments": {
      "your_targets": ["192.168.88.250", "192.168.88.251", ...],
      "ring_members": ["7CD30A7C5518", "843A5B91B661", "843A5B1FFFE4"],
      "assignment_epoch": 42
    }

  Assignment logic (server-side):
    1. Query all ONLINE appliances for the site (last_checkin < 5min)
    2. Build consistent hash ring from their MACs (same algorithm as daemon)
    3. Query all known scan targets for the site (windows_targets + linux_targets IPs)
    4. For each target IP: ring.OwnsTarget(checkin_appliance_MAC, IP) → include or exclude
    5. Return the list of IPs this appliance should scan

Daemon (cache + local optimization):
  On checkin response:
    1. Store target_assignments.your_targets as authoritative scan list
    2. Update local ring with ring_members (for local failover only)
    3. Use your_targets for scan cycle — NOT local OwnsTarget()
  
  Between checkins (local optimization, same-subnet only):
    - Continue ARP + gRPC probing for same-subnet peers
    - If a same-subnet peer goes offline (grace expired):
      → Re-evaluate local ring (peer removed → ring redistributes)
      → Targets that now map to self via local ring are scanned
      → This is "best effort" — may overlap with other surviving peers
    - Next checkin response corrects any drift with authoritative assignment
```

### What Changes

#### Backend (`sites.py` checkin handler)

Add a new step after STEP 3.8b (mesh peer delivery):

```python
# === STEP 3.8c: Server-side target assignment ===
target_assignments = {}
try:
    async with conn.transaction():
        # Get all online appliances for this site
        online_appliances = await conn.fetch("""
            SELECT appliance_id, mac_address
            FROM site_appliances
            WHERE site_id = $1
            AND status = 'online'
            AND last_checkin > NOW() - INTERVAL '5 minutes'
            ORDER BY mac_address
        """, checkin.site_id)
        
        if online_appliances:
            # Build server-side hash ring
            ring_macs = [normalize_mac(r['mac_address']) for r in online_appliances]
            this_mac = normalize_mac(checkin.mac_address)
            
            # Collect all scan target IPs
            all_target_ips = set()
            for t in windows_targets:
                if t.get('host'):
                    all_target_ips.add(t['host'])
            for t in linux_targets:
                if t.get('host'):
                    all_target_ips.add(t['host'])
            
            # Assign via consistent hash
            my_targets = []
            for ip in sorted(all_target_ips):
                owner = hash_ring_owner(ring_macs, ip)
                if owner == this_mac:
                    my_targets.append(ip)
            
            target_assignments = {
                "your_targets": my_targets,
                "ring_members": ring_macs,
                "assignment_epoch": int(time.time()),
            }
except Exception as e:
    logger.warning(f"Target assignment failed: {e}")
```

Add `hash_ring_owner()` utility — pure Python port of the Go consistent hash (SHA256, 64 replicas, same normalization). Must produce identical assignments.

Include in checkin response:
```python
"target_assignments": target_assignments,
```

#### Daemon (`daemon.go` / `mesh.go`)

**New: `ApplyTargetAssignment()`**

```go
// ApplyTargetAssignment stores server-authoritative target list.
// Called when checkin response includes target_assignments.
func (m *Mesh) ApplyTargetAssignment(targets []string, ringMembers []string, epoch int64) {
    m.mu.Lock()
    defer m.mu.Unlock()
    
    m.serverTargets = targets
    m.serverEpoch = epoch
    m.serverAssignmentTime = time.Now()
    
    // Update ring to match server's view (may differ from local probes)
    // This ensures local failover uses the same ring
    for _, mac := range ringMembers {
        m.ring.AddNode(mac)
    }
}
```

**Modified: `OwnsTarget()` — prefer server assignment**

```go
func (m *Mesh) OwnsTarget(targetIP string) bool {
    m.mu.RLock()
    defer m.mu.RUnlock()
    
    // Server-authoritative: if we have a recent assignment, use it
    if m.serverTargets != nil && time.Since(m.serverAssignmentTime) < 15*time.Minute {
        for _, ip := range m.serverTargets {
            if ip == targetIP {
                return true
            }
        }
        return false
    }
    
    // Fallback: local ring (no server assignment or stale)
    return m.ring.OwnsTarget(m.selfMAC, targetIP)
}
```

The 15-minute staleness threshold means: if the backend is unreachable for 15+ minutes, fall back to local ring. This preserves the current behavior as a degraded mode.

#### What Dies

| Component | Fate |
|-----------|------|
| `mesh_topology` site config (auto/independent) | **Remove.** Backend decides. |
| Network Stability onboarding gate | **Remove.** No longer needed — backend handles cross-subnet. |
| Split-brain detection/alerts | **Remove.** Single authority = no split-brain. |
| "Independent mode" notifications | **Remove.** |
| Cross-subnet gRPC probing | **Keep but demote.** Still useful for fast failover, but not required for correctness. |
| Local ARP-based peer discovery | **Keep.** Same-subnet optimization for sub-minute failover. |
| `UpdateBackendPeers()` | **Keep.** Still called, but ring is now a cache, not authority. |

#### Frontend Changes

- **Remove** Network Stability panel from SiteDetail.tsx (the "Choose One" amber box)
- **Remove** mesh_topology acknowledge button from ApplianceCard
- **Add** target assignment display to ApplianceCard: "Scanning: 3 targets" with expandable list
- **Remove** independent mode / split-brain notification types

### Migration

```sql
-- 127: target_assignment tracking
ALTER TABLE site_appliances
  ADD COLUMN IF NOT EXISTS assigned_targets JSONB DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS assignment_epoch BIGINT DEFAULT 0;

-- Drop mesh_topology (no longer meaningful)
-- Keep network_mode for DHCP/static reference but it no longer gates anything
```

### Hash Ring Compatibility

**Critical:** The Python server-side hash ring MUST produce identical assignments to the Go client-side ring. Both use:
- SHA256 of `"{MAC}:{i}"` for `i` in `0..63`
- First 4 bytes as big-endian uint32
- MAC normalized to uppercase, no separators
- Clockwise nearest-node assignment

A shared test vector file (`testdata/hash_ring_vectors.json`) ensures compatibility. Generated by Go, validated by Python:
```json
{
  "nodes": ["AABBCCDDEEFF", "112233445566", "7CD30A7C5518"],
  "replicas": 64,
  "targets": {
    "192.168.88.250": "<owner_mac>",
    "192.168.88.251": "<owner_mac>",
    "192.168.0.11": "<owner_mac>"
  }
}
```
Go test generates the file. Python test reads it and asserts identical owners. If either implementation changes, the test breaks. Vectors are committed to the repo.

### Evidence Deduplication

With server-side assignment, evidence dedup is straightforward:

```sql
-- Before inserting a compliance_bundle, check for recent duplicate
SELECT 1 FROM compliance_bundles
WHERE site_id = $1
  AND bundle_hash = $2
  AND created_at > NOW() - INTERVAL '15 minutes'
LIMIT 1;
```

If a duplicate exists (same hash within 15min), accept silently but don't insert. This handles the grace-period overlap window where two appliances might scan the same target during a failover transition.

### Testing Strategy

1. **Hash ring compatibility test** — Go test generates assignments for 10 targets across 3 MACs. Python test generates same. Compare outputs. Must be identical.

2. **Backend assignment unit tests** — Mock 3 appliances, 6 targets. Verify each appliance gets ~2 targets. Verify target coverage is complete (no gaps).

3. **Daemon fallback test** — Set server assignment, verify OwnsTarget uses it. Advance clock 16 min, verify fallback to local ring.

4. **Live integration test** — With 3 appliances at north-valley-branch-2:
   - Verify each gets a `target_assignments` in checkin response
   - Verify targets don't overlap between appliances
   - Verify T640 on 0.x gets assignments despite not seeing 88.x peers locally
   - Kill one appliance → next checkin cycle redistributes its targets

### Rollout

1. **Phase 1:** Backend generates `target_assignments` in checkin response. Daemon ignores it (new field, backward compatible).
2. **Phase 2:** Daemon reads `target_assignments`, uses as authority. Falls back to local ring if field missing (old backend compat).
3. **Phase 3:** Remove Network Stability UI, mesh_topology config, split-brain alerts. Clean up dead code.

Each phase is independently deployable. Phase 1 is backend-only (no daemon rebuild). Phase 2 requires fleet order to deploy new daemon. Phase 3 is frontend cleanup.

### Non-Goals

- **Leader election** — not needed. Backend is the leader.
- **Appliance-to-appliance data sync** — not needed. Backend mediates everything.
- **WireGuard mesh between appliances** — not needed. Backend handles cross-subnet coordination.
- **Quorum/consensus protocol** — not needed. Single authority model.

### Risk: Backend Down

If Central Command is unreachable for >15 minutes, appliances fall back to local hash ring. On the same subnet, this works perfectly (ARP discovery). Cross-subnet appliances fall back to scanning all targets (ring size 1 = owns everything). This may cause temporary evidence duplication, handled by dedup on recovery.

This is acceptable: if the backend is down for 15+ minutes, duplicate evidence is the least of the problems.
