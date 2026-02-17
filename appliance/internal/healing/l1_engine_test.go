package healing

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"gopkg.in/yaml.v3"
)

func TestBuiltinRuleCount(t *testing.T) {
	e := NewEngine("", nil)
	count := e.RuleCount()
	if count < 35 {
		t.Fatalf("expected at least 35 builtin rules, got %d", count)
	}
}

func TestBuiltinRulesSorted(t *testing.T) {
	e := NewEngine("", nil)
	rules := e.ListRules()

	for i := 1; i < len(rules); i++ {
		prev := rules[i-1]["priority"].(int)
		curr := rules[i]["priority"].(int)
		if prev > curr {
			t.Fatalf("rules not sorted: rule %d (priority %d) > rule %d (priority %d)",
				i-1, prev, i, curr)
		}
	}
}

func TestMatchFirewallDrift(t *testing.T) {
	e := NewEngine("", nil)

	data := map[string]interface{}{
		"check_type":     "firewall_status",
		"drift_detected": true,
	}

	m := e.Match("inc-001", "firewall_status", "high", data)
	if m == nil {
		t.Fatal("expected firewall match, got nil")
	}
	if m.Rule.ID != "L1-FW-002" {
		t.Fatalf("expected L1-FW-002, got %s", m.Rule.ID)
	}
	if m.Action != "run_windows_runbook" {
		t.Fatalf("expected run_windows_runbook, got %s", m.Action)
	}
}

func TestMatchEncryptionEscalate(t *testing.T) {
	e := NewEngine("", nil)

	data := map[string]interface{}{
		"check_type":     "encryption",
		"drift_detected": true,
	}

	m := e.Match("inc-002", "encryption", "critical", data)
	if m == nil {
		t.Fatal("expected encryption match, got nil")
	}
	if m.Rule.ID != "L1-ENCRYPT-001" {
		t.Fatalf("expected L1-ENCRYPT-001, got %s", m.Rule.ID)
	}
	if m.Action != "escalate" {
		t.Fatalf("expected escalate action, got %s", m.Action)
	}
}

func TestMatchNoMatch(t *testing.T) {
	e := NewEngine("", nil)

	data := map[string]interface{}{
		"check_type":     "unknown_check",
		"drift_detected": true,
	}

	m := e.Match("inc-003", "unknown_check", "low", data)
	if m != nil {
		t.Fatalf("expected no match, got rule %s", m.Rule.ID)
	}
}

func TestMatchNoDrift(t *testing.T) {
	e := NewEngine("", nil)

	data := map[string]interface{}{
		"check_type":     "firewall_status",
		"drift_detected": false,
	}

	m := e.Match("inc-004", "firewall_status", "high", data)
	if m != nil {
		t.Fatalf("expected no match when drift_detected=false, got %s", m.Rule.ID)
	}
}

func TestMatchDisabledRule(t *testing.T) {
	e := NewEngine("", nil)

	// Disable all rules
	e.mu.Lock()
	for _, r := range e.rules {
		r.Enabled = false
	}
	e.mu.Unlock()

	data := map[string]interface{}{
		"check_type":     "firewall_status",
		"drift_detected": true,
	}

	m := e.Match("inc-005", "firewall_status", "high", data)
	if m != nil {
		t.Fatalf("expected no match when rules disabled, got %s", m.Rule.ID)
	}
}

func TestMatchCooldown(t *testing.T) {
	e := NewEngine("", nil)

	data := map[string]interface{}{
		"check_type":     "firewall_status",
		"drift_detected": true,
		"host_id":        "ws01",
	}

	// First match should succeed
	m1 := e.Match("inc-006", "firewall_status", "high", data)
	if m1 == nil {
		t.Fatal("expected first match, got nil")
	}

	// Set cooldown (simulate recent execution)
	e.mu.Lock()
	e.cooldowns["L1-FW-002:ws01"] = time.Now()
	e.mu.Unlock()

	// Second match should be blocked by cooldown
	m2 := e.Match("inc-007", "firewall_status", "high", data)
	if m2 != nil {
		t.Fatalf("expected cooldown block, but got match %s", m2.Rule.ID)
	}
}

func TestMatchNestedField(t *testing.T) {
	e := NewEngine("", nil)

	data := map[string]interface{}{
		"incident_type": "disk_space",
		"details": map[string]interface{}{
			"usage_percent": float64(95),
		},
	}

	m := e.Match("inc-008", "disk_space", "warning", data)
	if m == nil {
		t.Fatal("expected disk space match with nested field, got nil")
	}
	if m.Rule.ID != "L1-DISK-001" {
		t.Fatalf("expected L1-DISK-001, got %s", m.Rule.ID)
	}
}

func TestMatchGreaterThan(t *testing.T) {
	e := NewEngine("", nil)

	// Below threshold: should NOT match
	data := map[string]interface{}{
		"incident_type": "disk_space",
		"details": map[string]interface{}{
			"usage_percent": float64(80),
		},
	}

	m := e.Match("inc-009", "disk_space", "warning", data)
	if m != nil {
		t.Fatalf("expected no match for 80%% usage, got %s", m.Rule.ID)
	}
}

func TestMatchLessThan(t *testing.T) {
	e := NewEngine("", nil)

	data := map[string]interface{}{
		"incident_type": "cert_expiry",
		"details": map[string]interface{}{
			"days_remaining": float64(15),
		},
	}

	m := e.Match("inc-010", "cert_expiry", "warning", data)
	if m == nil {
		t.Fatal("expected cert expiry match, got nil")
	}
	if m.Rule.ID != "L1-CERT-001" {
		t.Fatalf("expected L1-CERT-001, got %s", m.Rule.ID)
	}
}

func TestExecuteDryRun(t *testing.T) {
	e := NewEngine("", nil) // nil executor = dry run

	data := map[string]interface{}{
		"check_type":     "firewall_status",
		"drift_detected": true,
		"host_id":        "ws-dry",
	}

	m := e.Match("inc-011", "firewall_status", "high", data)
	if m == nil {
		t.Fatal("expected match, got nil")
	}

	result := e.Execute(m, "site-01", "ws-dry")
	if !result.Success {
		t.Fatal("expected dry run success")
	}
	if result.Output != "DRY_RUN" {
		t.Fatalf("expected DRY_RUN output, got %v", result.Output)
	}
	if result.DurationMs < 0 {
		t.Fatal("expected non-negative duration")
	}
}

func TestExecuteWithExecutor(t *testing.T) {
	executor := func(action string, params map[string]interface{}, siteID, hostID string) (map[string]interface{}, error) {
		return map[string]interface{}{
			"success": true,
			"message": "healed",
		}, nil
	}

	e := NewEngine("", executor)

	data := map[string]interface{}{
		"check_type":     "firewall_status",
		"drift_detected": true,
		"host_id":        "ws-exec",
	}

	m := e.Match("inc-012", "firewall_status", "high", data)
	if m == nil {
		t.Fatal("expected match, got nil")
	}

	result := e.Execute(m, "site-01", "ws-exec")
	if !result.Success {
		t.Fatalf("expected success, got error: %s", result.Error)
	}
}

func TestLoadYAMLRules(t *testing.T) {
	dir := t.TempDir()

	rule := map[string]interface{}{
		"id":          "CUSTOM-001",
		"name":        "Custom Test Rule",
		"description": "Test rule from YAML",
		"conditions": []interface{}{
			map[string]interface{}{
				"field":    "check_type",
				"operator": "eq",
				"value":    "custom_check",
			},
			map[string]interface{}{
				"field":    "drift_detected",
				"operator": "eq",
				"value":    true,
			},
		},
		"action":          "custom_action",
		"action_params":   map[string]interface{}{"key": "value"},
		"hipaa_controls":  []interface{}{"164.312(a)(1)"},
		"enabled":         true,
		"priority":        1,
		"cooldown_seconds": 60,
	}

	data, _ := yaml.Marshal(rule)
	os.WriteFile(filepath.Join(dir, "custom.yaml"), data, 0o644)

	e := NewEngine(dir, nil)

	testData := map[string]interface{}{
		"check_type":     "custom_check",
		"drift_detected": true,
	}

	m := e.Match("inc-013", "custom_check", "high", testData)
	if m == nil {
		t.Fatal("expected custom rule match, got nil")
	}
	if m.Rule.ID != "CUSTOM-001" {
		t.Fatalf("expected CUSTOM-001, got %s", m.Rule.ID)
	}
	if m.Rule.Source != "custom" {
		t.Fatalf("expected source=custom, got %s", m.Rule.Source)
	}
}

func TestLoadMultipleYAMLRules(t *testing.T) {
	dir := t.TempDir()

	rules := map[string]interface{}{
		"rules": []interface{}{
			map[string]interface{}{
				"id":   "MULTI-001",
				"name": "Multi Rule 1",
				"conditions": []interface{}{
					map[string]interface{}{"field": "check_type", "operator": "eq", "value": "multi1"},
					map[string]interface{}{"field": "drift_detected", "operator": "eq", "value": true},
				},
				"action":   "action1",
				"priority": 1,
			},
			map[string]interface{}{
				"id":   "MULTI-002",
				"name": "Multi Rule 2",
				"conditions": []interface{}{
					map[string]interface{}{"field": "check_type", "operator": "eq", "value": "multi2"},
					map[string]interface{}{"field": "drift_detected", "operator": "eq", "value": true},
				},
				"action":   "action2",
				"priority": 2,
			},
		},
	}

	data, _ := yaml.Marshal(rules)
	os.WriteFile(filepath.Join(dir, "multi.yaml"), data, 0o644)

	e := NewEngine(dir, nil)

	m1 := e.Match("inc-014", "multi1", "high", map[string]interface{}{
		"check_type": "multi1", "drift_detected": true,
	})
	if m1 == nil || m1.Rule.ID != "MULTI-001" {
		t.Fatal("expected MULTI-001 match")
	}

	m2 := e.Match("inc-015", "multi2", "high", map[string]interface{}{
		"check_type": "multi2", "drift_detected": true,
	})
	if m2 == nil || m2.Rule.ID != "MULTI-002" {
		t.Fatal("expected MULTI-002 match")
	}
}

func TestLoadSyncedJSONRules(t *testing.T) {
	dir := t.TempDir()

	rules := []map[string]interface{}{
		{
			"id":   "SYNCED-001",
			"name": "Synced Rule",
			"conditions": []interface{}{
				map[string]interface{}{"field": "check_type", "operator": "eq", "value": "synced_check"},
				map[string]interface{}{"field": "drift_detected", "operator": "eq", "value": true},
			},
			"actions":  []interface{}{"synced_action"},
			"priority": 2,
		},
	}

	data, _ := json.Marshal(rules)
	os.WriteFile(filepath.Join(dir, "l1_rules.json"), data, 0o644)

	e := NewEngine(dir, nil)

	m := e.Match("inc-016", "synced_check", "high", map[string]interface{}{
		"check_type": "synced_check", "drift_detected": true,
	})
	if m == nil {
		t.Fatal("expected synced rule match, got nil")
	}
	if m.Rule.ID != "SYNCED-001" {
		t.Fatalf("expected SYNCED-001, got %s", m.Rule.ID)
	}
	if m.Rule.Source != "synced" {
		t.Fatalf("expected source=synced, got %s", m.Rule.Source)
	}
	if m.Action != "synced_action" {
		t.Fatalf("expected synced_action, got %s", m.Action)
	}
}

func TestSyncedRulesOverrideBuiltin(t *testing.T) {
	dir := t.TempDir()

	// Synced rule with priority 2 should override builtin priority 5
	rules := []map[string]interface{}{
		{
			"id":   "SYNCED-FW",
			"name": "Synced Firewall",
			"conditions": []interface{}{
				map[string]interface{}{"field": "check_type", "operator": "eq", "value": "firewall_status"},
				map[string]interface{}{"field": "drift_detected", "operator": "eq", "value": true},
			},
			"actions":  []interface{}{"synced_fw_action"},
			"priority": 2,
		},
	}

	data, _ := json.Marshal(rules)
	os.WriteFile(filepath.Join(dir, "l1_rules.json"), data, 0o644)

	e := NewEngine(dir, nil)

	m := e.Match("inc-017", "firewall_status", "high", map[string]interface{}{
		"check_type": "firewall_status", "drift_detected": true,
	})
	if m == nil {
		t.Fatal("expected match, got nil")
	}
	// Synced rule (priority 2) should win over builtin L1-FW-002 (priority 5)
	if m.Rule.ID != "SYNCED-FW" {
		t.Fatalf("expected SYNCED-FW to override builtin, got %s", m.Rule.ID)
	}
}

func TestConditionOperators(t *testing.T) {
	tests := []struct {
		name     string
		cond     RuleCondition
		data     map[string]interface{}
		expected bool
	}{
		{
			name:     "equals string",
			cond:     RuleCondition{Field: "type", Operator: OpEquals, Value: "test"},
			data:     map[string]interface{}{"type": "test"},
			expected: true,
		},
		{
			name:     "not equals",
			cond:     RuleCondition{Field: "type", Operator: OpNotEquals, Value: "other"},
			data:     map[string]interface{}{"type": "test"},
			expected: true,
		},
		{
			name:     "contains",
			cond:     RuleCondition{Field: "msg", Operator: OpContains, Value: "error"},
			data:     map[string]interface{}{"msg": "fatal error occurred"},
			expected: true,
		},
		{
			name:     "regex",
			cond:     RuleCondition{Field: "version", Operator: OpRegex, Value: `^\d+\.\d+`},
			data:     map[string]interface{}{"version": "3.14.159"},
			expected: true,
		},
		{
			name:     "greater than",
			cond:     RuleCondition{Field: "count", Operator: OpGreaterThan, Value: float64(10)},
			data:     map[string]interface{}{"count": float64(15)},
			expected: true,
		},
		{
			name:     "less than",
			cond:     RuleCondition{Field: "count", Operator: OpLessThan, Value: float64(10)},
			data:     map[string]interface{}{"count": float64(5)},
			expected: true,
		},
		{
			name:     "in list",
			cond:     RuleCondition{Field: "status", Operator: OpIn, Value: []interface{}{"pass", "warn"}},
			data:     map[string]interface{}{"status": "warn"},
			expected: true,
		},
		{
			name:     "not in list",
			cond:     RuleCondition{Field: "status", Operator: OpNotIn, Value: []interface{}{"pass", "warn"}},
			data:     map[string]interface{}{"status": "fail"},
			expected: true,
		},
		{
			name:     "exists true",
			cond:     RuleCondition{Field: "key", Operator: OpExists, Value: true},
			data:     map[string]interface{}{"key": "value"},
			expected: true,
		},
		{
			name:     "exists false",
			cond:     RuleCondition{Field: "missing", Operator: OpExists, Value: true},
			data:     map[string]interface{}{"key": "value"},
			expected: false,
		},
		{
			name:     "not exists",
			cond:     RuleCondition{Field: "missing", Operator: OpExists, Value: false},
			data:     map[string]interface{}{"key": "value"},
			expected: true,
		},
		{
			name:     "nested dot notation",
			cond:     RuleCondition{Field: "a.b.c", Operator: OpEquals, Value: "deep"},
			data:     map[string]interface{}{"a": map[string]interface{}{"b": map[string]interface{}{"c": "deep"}}},
			expected: true,
		},
		{
			name:     "nil field returns false for eq",
			cond:     RuleCondition{Field: "missing", Operator: OpEquals, Value: "x"},
			data:     map[string]interface{}{},
			expected: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := tt.cond.Matches(tt.data)
			if result != tt.expected {
				t.Errorf("expected %v, got %v", tt.expected, result)
			}
		})
	}
}

func TestSeverityFilter(t *testing.T) {
	rule := &Rule{
		ID:      "TEST-SEV",
		Enabled: true,
		Conditions: []RuleCondition{
			{Field: "check_type", Operator: OpEquals, Value: "test"},
		},
		SeverityFilter: []string{"high", "critical"},
	}

	// Should match high
	if !rule.Matches("test", "high", map[string]interface{}{"check_type": "test"}) {
		t.Fatal("expected match for high severity")
	}

	// Should not match low
	if rule.Matches("test", "low", map[string]interface{}{"check_type": "test"}) {
		t.Fatal("expected no match for low severity")
	}
}

func TestStats(t *testing.T) {
	e := NewEngine("", nil)
	stats := e.Stats()

	total, _ := stats["total_rules"].(int)
	if total < 35 {
		t.Fatalf("expected at least 35 rules in stats, got %d", total)
	}

	bySource, _ := stats["by_source"].(map[string]int)
	if bySource["builtin"] < 35 {
		t.Fatalf("expected at least 35 builtin rules, got %d", bySource["builtin"])
	}
}

func TestReloadRules(t *testing.T) {
	dir := t.TempDir()
	e := NewEngine(dir, nil)
	initialCount := e.RuleCount()

	// Write a new rule file
	rule := map[string]interface{}{
		"id":   "RELOAD-001",
		"name": "Reload Test",
		"conditions": []interface{}{
			map[string]interface{}{"field": "check_type", "operator": "eq", "value": "reload"},
			map[string]interface{}{"field": "drift_detected", "operator": "eq", "value": true},
		},
		"action":   "test",
		"priority": 1,
	}
	data, _ := yaml.Marshal(rule)
	os.WriteFile(filepath.Join(dir, "reload.yaml"), data, 0o644)

	e.ReloadRules()
	newCount := e.RuleCount()

	if newCount != initialCount+1 {
		t.Fatalf("expected %d rules after reload, got %d", initialCount+1, newCount)
	}
}

func TestLinuxRulesMatch(t *testing.T) {
	e := NewEngine("", nil)

	tests := []struct {
		checkType  string
		expectedID string
	}{
		{"ssh_config", "L1-SSH-001"},
		{"kernel", "L1-KERN-001"},
		{"cron", "L1-CRON-001"},
		{"audit", "L1-LIN-AUDIT-001"},
		{"crypto", "L1-LIN-CRYPTO-001"},
		{"incident_response", "L1-LIN-IR-001"},
		{"banner", "L1-LIN-BANNER-001"},
		{"network", "L1-LIN-NET-001"},
	}

	for _, tt := range tests {
		t.Run(tt.checkType, func(t *testing.T) {
			data := map[string]interface{}{
				"check_type":     tt.checkType,
				"drift_detected": true,
			}
			m := e.Match("inc", tt.checkType, "high", data)
			if m == nil {
				t.Fatalf("expected match for %s, got nil", tt.checkType)
			}
			if m.Rule.ID != tt.expectedID {
				t.Fatalf("expected %s, got %s", tt.expectedID, m.Rule.ID)
			}
		})
	}
}

func TestWindowsRulesMatch(t *testing.T) {
	e := NewEngine("", nil)

	tests := []struct {
		checkType  string
		expectedID string
	}{
		{"service_dns", "L1-WIN-SVC-DNS"},
		{"smb_signing", "L1-WIN-SEC-SMB"},
		{"service_wuauserv", "L1-WIN-SVC-WUAUSERV"},
		{"network_profile", "L1-WIN-NET-PROFILE"},
		{"screen_lock_policy", "L1-WIN-SEC-SCREENLOCK"},
		{"bitlocker_status", "L1-WIN-SEC-BITLOCKER"},
		{"service_netlogon", "L1-WIN-SVC-NETLOGON"},
		{"dns_config", "L1-WIN-DNS-HIJACK"},
		{"defender_exclusions", "L1-WIN-SEC-DEFENDER-EXCL"},
		{"scheduled_task_persistence", "L1-PERSIST-TASK-001"},
		{"registry_run_persistence", "L1-PERSIST-REG-001"},
		{"smb1_protocol", "L1-WIN-SEC-SMB1"},
		{"wmi_event_persistence", "L1-PERSIST-WMI-001"},
	}

	for _, tt := range tests {
		t.Run(tt.checkType, func(t *testing.T) {
			data := map[string]interface{}{
				"check_type":     tt.checkType,
				"drift_detected": true,
			}
			m := e.Match("inc", tt.checkType, "high", data)
			if m == nil {
				t.Fatalf("expected match for %s, got nil", tt.checkType)
			}
			if m.Rule.ID != tt.expectedID {
				t.Fatalf("expected %s, got %s", tt.expectedID, m.Rule.ID)
			}
		})
	}
}
