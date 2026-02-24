// Package healing implements the L1 deterministic rules engine.
//
// Handles 70-80% of incidents with:
//   - Sub-100ms response time
//   - Zero LLM cost
//   - Predictable, auditable behavior
//   - YAML/JSON rule definitions
//
// Rules are loaded from:
//  1. Built-in default rules
//  2. Bundled rules directory (package-level)
//  3. Custom rules directory (site-level)
//  4. Synced JSON rules (from Central Command)
//  5. Promoted rules (from L2 learning engine)
package healing

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/osiriscare/appliance/internal/crypto"
	"gopkg.in/yaml.v3"
)

// MatchOperator defines comparison operators for rule conditions.
type MatchOperator string

const (
	OpEquals      MatchOperator = "eq"
	OpNotEquals   MatchOperator = "ne"
	OpContains    MatchOperator = "contains"
	OpRegex       MatchOperator = "regex"
	OpGreaterThan MatchOperator = "gt"
	OpLessThan    MatchOperator = "lt"
	OpIn          MatchOperator = "in"
	OpNotIn       MatchOperator = "not_in"
	OpExists      MatchOperator = "exists"
)

// RuleCondition is a single condition in a rule.
type RuleCondition struct {
	Field    string        `json:"field" yaml:"field"`
	Operator MatchOperator `json:"operator" yaml:"operator"`
	Value    interface{}   `json:"value" yaml:"value"`
}

// Matches checks if this condition matches the given data.
func (c *RuleCondition) Matches(data map[string]interface{}) bool {
	actual := getFieldValue(data, c.Field)

	// EXISTS checks whether the field is present (not nil)
	if c.Operator == OpExists {
		fieldExists := actual != nil
		if boolVal, ok := c.Value.(bool); ok {
			return fieldExists == boolVal
		}
		return fieldExists
	}

	if actual == nil {
		return false
	}

	switch c.Operator {
	case OpEquals:
		return valuesEqual(actual, c.Value)
	case OpNotEquals:
		return !valuesEqual(actual, c.Value)
	case OpContains:
		return strings.Contains(fmt.Sprintf("%v", actual), fmt.Sprintf("%v", c.Value))
	case OpRegex:
		pattern := fmt.Sprintf("%v", c.Value)
		re, err := regexp.Compile(pattern)
		if err != nil {
			return false
		}
		return re.MatchString(fmt.Sprintf("%v", actual))
	case OpGreaterThan:
		af, aOK := toFloat(actual)
		vf, vOK := toFloat(c.Value)
		return aOK && vOK && af > vf
	case OpLessThan:
		af, aOK := toFloat(actual)
		vf, vOK := toFloat(c.Value)
		return aOK && vOK && af < vf
	case OpIn:
		return valueIn(actual, c.Value)
	case OpNotIn:
		return !valueIn(actual, c.Value)
	}

	return false
}

// Rule is a deterministic rule for incident handling.
type Rule struct {
	ID              string                 `json:"id" yaml:"id"`
	Name            string                 `json:"name" yaml:"name"`
	Description     string                 `json:"description" yaml:"description"`
	Conditions      []RuleCondition        `json:"conditions" yaml:"conditions"`
	Action          string                 `json:"action" yaml:"action"`
	ActionParams    map[string]interface{} `json:"action_params" yaml:"action_params"`
	HIPAAControls   []string               `json:"hipaa_controls" yaml:"hipaa_controls"`
	SeverityFilter  []string               `json:"severity_filter" yaml:"severity_filter"`
	Enabled         bool                   `json:"enabled" yaml:"enabled"`
	Priority        int                    `json:"priority" yaml:"priority"`
	CooldownSeconds int                    `json:"cooldown_seconds" yaml:"cooldown_seconds"`
	MaxRetries      int                    `json:"max_retries" yaml:"max_retries"`
	Source          string                 `json:"source" yaml:"source"`
	GPOManaged      bool                   `json:"gpo_managed" yaml:"gpo_managed"`
}

// Matches checks if this rule matches an incident.
func (r *Rule) Matches(incidentType, severity string, data map[string]interface{}) bool {
	if !r.Enabled {
		return false
	}

	if len(r.SeverityFilter) > 0 {
		found := false
		for _, s := range r.SeverityFilter {
			if s == severity {
				found = true
				break
			}
		}
		if !found {
			return false
		}
	}

	// All conditions must match (AND logic)
	for _, cond := range r.Conditions {
		if !cond.Matches(data) {
			return false
		}
	}

	return true
}

// RuleMatch is the result of a successful rule match.
type RuleMatch struct {
	Rule         *Rule
	IncidentID   string
	MatchedAt    string
	Action       string
	ActionParams map[string]interface{}
}

// ExecutionResult is the result of executing a matched rule's action.
type ExecutionResult struct {
	RuleID      string                 `json:"rule_id"`
	IncidentID  string                 `json:"incident_id"`
	Action      string                 `json:"action"`
	StartedAt   string                 `json:"started_at"`
	CompletedAt string                 `json:"completed_at,omitempty"`
	DurationMs  int64                  `json:"duration_ms,omitempty"`
	Success     bool                   `json:"success"`
	Output      interface{}            `json:"output,omitempty"`
	Error       string                 `json:"error,omitempty"`
	Params      map[string]interface{} `json:"params,omitempty"`
}

// ActionExecutor is a callback function that executes a healing action.
type ActionExecutor func(action string, params map[string]interface{}, siteID, hostID string) (map[string]interface{}, error)

// Engine is the L1 deterministic rules engine.
type Engine struct {
	rulesDir       string
	rules          []*Rule
	cooldowns      map[string]time.Time // "rule_id:host_id" -> last execution
	mu             sync.RWMutex
	actionExecutor ActionExecutor
	verifier       *crypto.OrderVerifier // Verifies signed rules from Central Command
}

// NewEngine creates a new L1 deterministic engine.
func NewEngine(rulesDir string, executor ActionExecutor) *Engine {
	e := &Engine{
		rulesDir:       rulesDir,
		cooldowns:      make(map[string]time.Time),
		actionExecutor: executor,
		verifier:       crypto.NewOrderVerifier(""),
	}
	e.LoadRules()
	return e
}

// SetServerPublicKey sets the Ed25519 public key for verifying signed rules.
func (e *Engine) SetServerPublicKey(hexKey string) error {
	return e.verifier.SetPublicKey(hexKey)
}

// LoadRules loads all rules from builtins and disk.
func (e *Engine) LoadRules() {
	e.mu.Lock()
	defer e.mu.Unlock()

	e.rules = nil

	// 1. Built-in rules
	e.rules = append(e.rules, builtinRules()...)

	// 2. Custom YAML rules from rules directory
	if e.rulesDir != "" {
		e.loadYAMLRules(e.rulesDir)

		// 3. Synced JSON rules from Central Command
		e.loadSyncedJSONRules(e.rulesDir)

		// 4. Promoted rules from learning engine
		promotedDir := filepath.Join(e.rulesDir, "promoted")
		e.loadYAMLRules(promotedDir)
	}

	// Sort by priority (lower = higher priority)
	sort.Slice(e.rules, func(i, j int) bool {
		return e.rules[i].Priority < e.rules[j].Priority
	})

	log.Printf("[l1] Loaded %d rules", len(e.rules))
}

// ReloadRules reloads rules from disk.
func (e *Engine) ReloadRules() {
	e.LoadRules()
}

// Match finds the first matching rule for an incident.
// Returns nil if no rule matches (should escalate to L2).
func (e *Engine) Match(incidentID, incidentType, severity string, data map[string]interface{}) *RuleMatch {
	e.mu.RLock()
	defer e.mu.RUnlock()

	for _, rule := range e.rules {
		if !rule.Matches(incidentType, severity, data) {
			continue
		}

		// Check cooldown
		hostID, _ := data["host_id"].(string)
		if hostID == "" {
			hostID = "unknown"
		}
		cooldownKey := rule.ID + ":" + hostID

		if lastExec, ok := e.cooldowns[cooldownKey]; ok {
			elapsed := time.Since(lastExec).Seconds()
			if elapsed < float64(rule.CooldownSeconds) {
				log.Printf("[l1] Rule %s in cooldown (%.0fs < %ds)",
					rule.ID, elapsed, rule.CooldownSeconds)
				continue
			}
		}

		return &RuleMatch{
			Rule:         rule,
			IncidentID:   incidentID,
			MatchedAt:    time.Now().UTC().Format(time.RFC3339),
			Action:       rule.Action,
			ActionParams: rule.ActionParams,
		}
	}

	return nil
}

// Execute runs a matched rule's action.
func (e *Engine) Execute(match *RuleMatch, siteID, hostID string) *ExecutionResult {
	start := time.Now().UTC()
	result := &ExecutionResult{
		RuleID:     match.Rule.ID,
		IncidentID: match.IncidentID,
		Action:     match.Action,
		StartedAt:  start.Format(time.RFC3339),
		Params:     match.ActionParams,
	}

	// Update cooldown
	cooldownKey := match.Rule.ID + ":" + hostID
	e.mu.Lock()
	e.cooldowns[cooldownKey] = start
	e.mu.Unlock()

	if e.actionExecutor == nil {
		log.Printf("[l1] No action executor configured, dry run: %s", match.Action)
		result.Output = "DRY_RUN"
		result.Success = true
		result.CompletedAt = time.Now().UTC().Format(time.RFC3339)
		result.DurationMs = time.Since(start).Milliseconds()
		return result
	}

	output, err := e.actionExecutor(match.Action, match.ActionParams, siteID, hostID)
	if err != nil {
		log.Printf("[l1] Rule execution failed: %v", err)
		result.Error = err.Error()
		result.CompletedAt = time.Now().UTC().Format(time.RFC3339)
		result.DurationMs = time.Since(start).Milliseconds()
		return result
	}

	result.Output = output
	if output != nil {
		if s, ok := output["success"]; ok {
			if bv, ok := s.(bool); ok {
				result.Success = bv
			}
		} else {
			result.Success = true
		}
		if e, ok := output["error"]; ok {
			if ev, ok := e.(string); ok {
				result.Error = ev
			}
		}
	} else {
		result.Success = true
	}

	result.CompletedAt = time.Now().UTC().Format(time.RFC3339)
	result.DurationMs = time.Since(start).Milliseconds()

	return result
}

// Stats returns statistics about loaded rules.
func (e *Engine) Stats() map[string]interface{} {
	e.mu.RLock()
	defer e.mu.RUnlock()

	bySource := map[string]int{"builtin": 0, "custom": 0, "promoted": 0, "synced": 0}
	byAction := map[string]int{}
	enabled := 0

	for _, r := range e.rules {
		bySource[r.Source]++
		byAction[r.Action]++
		if r.Enabled {
			enabled++
		}
	}

	return map[string]interface{}{
		"total_rules":      len(e.rules),
		"enabled_rules":    enabled,
		"by_source":        bySource,
		"by_action":        byAction,
		"active_cooldowns": len(e.cooldowns),
	}
}

// ListRules returns all rules with their details.
func (e *Engine) ListRules() []map[string]interface{} {
	e.mu.RLock()
	defer e.mu.RUnlock()

	result := make([]map[string]interface{}, len(e.rules))
	for i, r := range e.rules {
		result[i] = map[string]interface{}{
			"id":             r.ID,
			"name":           r.Name,
			"description":    r.Description,
			"action":         r.Action,
			"priority":       r.Priority,
			"enabled":        r.Enabled,
			"source":         r.Source,
			"hipaa_controls": r.HIPAAControls,
		}
	}
	return result
}

// RuleCount returns the number of loaded rules.
func (e *Engine) RuleCount() int {
	e.mu.RLock()
	defer e.mu.RUnlock()
	return len(e.rules)
}

// --- Rule loading helpers ---

func (e *Engine) loadYAMLRules(dir string) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return
	}

	// Sort for deterministic order
	sort.Slice(entries, func(i, j int) bool {
		return entries[i].Name() < entries[j].Name()
	})

	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		name := entry.Name()
		if !strings.HasSuffix(name, ".yaml") && !strings.HasSuffix(name, ".yml") {
			continue
		}

		path := filepath.Join(dir, name)
		data, err := os.ReadFile(path)
		if err != nil {
			log.Printf("[l1] Failed to read rule file %s: %v", path, err)
			continue
		}

		var raw map[string]interface{}
		if err := yaml.Unmarshal(data, &raw); err != nil {
			log.Printf("[l1] Failed to parse rule file %s: %v", path, err)
			continue
		}

		if rulesRaw, ok := raw["rules"]; ok {
			// Multiple rules in one file
			if rulesList, ok := rulesRaw.([]interface{}); ok {
				for _, rr := range rulesList {
					if rd, ok := rr.(map[string]interface{}); ok {
						if r := ruleFromMap(rd, "custom"); r != nil {
							e.rules = append(e.rules, r)
						}
					}
				}
			}
		} else {
			// Single rule
			if r := ruleFromMap(raw, "custom"); r != nil {
				e.rules = append(e.rules, r)
			}
		}

		log.Printf("[l1] Loaded rules from %s", name)
	}
}

func (e *Engine) loadSyncedJSONRules(dir string) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return
	}

	sort.Slice(entries, func(i, j int) bool {
		return entries[i].Name() < entries[j].Name()
	})

	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}

		path := filepath.Join(dir, entry.Name())
		data, err := os.ReadFile(path)
		if err != nil {
			log.Printf("[l1] Failed to read synced rules %s: %v", path, err)
			continue
		}

		// Try array format first (standard sync format)
		var rulesList []map[string]interface{}
		if err := json.Unmarshal(data, &rulesList); err == nil {
			for _, rd := range rulesList {
				if r := ruleFromSyncedJSON(rd); r != nil {
					e.rules = append(e.rules, r)
				}
			}
			log.Printf("[l1] Loaded %d synced rules from %s", len(rulesList), entry.Name())
			continue
		}

		// Try wrapped format with "rules" key (includes optional signature)
		var wrapped map[string]interface{}
		if err := json.Unmarshal(data, &wrapped); err == nil {
			// Verify signature if present
			sigHex, _ := wrapped["signature"].(string)
			if sigHex != "" && e.verifier.HasKey() {
				// Reconstruct the canonical rules JSON for verification
				rulesBytes, _ := json.Marshal(wrapped["rules"])
				// Python uses json.dumps(all_rules, sort_keys=True)
				// Re-parse and re-serialize with sort_keys for determinism
				var rulesForVerify interface{}
				json.Unmarshal(rulesBytes, &rulesForVerify)
				canonicalRules, _ := jsonMarshalSorted(rulesForVerify)
				if err := e.verifier.VerifyRulesBundle(string(canonicalRules), sigHex); err != nil {
					log.Printf("[l1] SECURITY: Rules signature verification failed for %s: %v — skipping",
						entry.Name(), err)
					continue
				}
				log.Printf("[l1] Rules signature verified for %s", entry.Name())
			} else if sigHex == "" && e.verifier.HasKey() {
				log.Printf("[l1] WARNING: unsigned rules file %s — will be rejected after rollout", entry.Name())
			}

			// Update server public key if provided in the rules bundle
			if pubKey, ok := wrapped["server_public_key"].(string); ok && pubKey != "" {
				if err := e.verifier.SetPublicKey(pubKey); err != nil {
					log.Printf("[l1] Failed to set server public key from rules: %v", err)
				}
			}

			if rulesRaw, ok := wrapped["rules"]; ok {
				if arr, ok := rulesRaw.([]interface{}); ok {
					for _, rr := range arr {
						if rd, ok := rr.(map[string]interface{}); ok {
							if r := ruleFromSyncedJSON(rd); r != nil {
								e.rules = append(e.rules, r)
							}
						}
					}
				}
			}
		}
	}
}

// --- Value comparison helpers ---

func getFieldValue(data map[string]interface{}, field string) interface{} {
	parts := strings.Split(field, ".")
	var current interface{} = data

	for _, part := range parts {
		m, ok := current.(map[string]interface{})
		if !ok {
			return nil
		}
		current = m[part]
	}

	return current
}

func valuesEqual(a, b interface{}) bool {
	// Handle bool/bool comparison
	ab, aIsBool := a.(bool)
	bb, bIsBool := b.(bool)
	if aIsBool && bIsBool {
		return ab == bb
	}

	// Handle numeric comparisons (JSON/YAML may decode as float64 or int)
	af, aOK := toFloat(a)
	bf, bOK := toFloat(b)
	if aOK && bOK {
		return af == bf
	}

	// String comparison
	return fmt.Sprintf("%v", a) == fmt.Sprintf("%v", b)
}

func toFloat(v interface{}) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case float32:
		return float64(n), true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	case string:
		f, err := strconv.ParseFloat(n, 64)
		return f, err == nil
	}
	return 0, false
}

func valueIn(actual, list interface{}) bool {
	arr, ok := list.([]interface{})
	if !ok {
		return false
	}
	for _, item := range arr {
		if valuesEqual(actual, item) {
			return true
		}
	}
	return false
}

// --- Rule constructors ---

func ruleFromMap(m map[string]interface{}, source string) *Rule {
	id, _ := m["id"].(string)
	if id == "" {
		return nil
	}

	r := &Rule{
		ID:              id,
		Name:            strOrDefault(m, "name", id),
		Description:     strOrDefault(m, "description", ""),
		Action:          strOrDefault(m, "action", ""),
		ActionParams:    mapOrEmpty(m, "action_params"),
		HIPAAControls:   strSlice(m, "hipaa_controls"),
		SeverityFilter:  strSlice(m, "severity_filter"),
		Enabled:         boolOrDefault(m, "enabled", true),
		Priority:        intOrDefault(m, "priority", 100),
		CooldownSeconds: intOrDefault(m, "cooldown_seconds", 300),
		MaxRetries:      intOrDefault(m, "max_retries", 1),
		Source:          source,
		GPOManaged:      boolOrDefault(m, "gpo_managed", false),
	}

	if conds, ok := m["conditions"].([]interface{}); ok {
		for _, c := range conds {
			if cm, ok := c.(map[string]interface{}); ok {
				r.Conditions = append(r.Conditions, RuleCondition{
					Field:    strOrDefault(cm, "field", ""),
					Operator: MatchOperator(strOrDefault(cm, "operator", "eq")),
					Value:    cm["value"],
				})
			}
		}
	}

	return r
}

func ruleFromSyncedJSON(m map[string]interface{}) *Rule {
	id, _ := m["id"].(string)
	if id == "" {
		return nil
	}

	// Convert 'actions' list to 'action' string (use first action)
	action := ""
	if actions, ok := m["actions"].([]interface{}); ok && len(actions) > 0 {
		action, _ = actions[0].(string)
	}
	if action == "" {
		action = strOrDefault(m, "action", "alert:unknown")
	}

	r := &Rule{
		ID:              id,
		Name:            strOrDefault(m, "name", id),
		Description:     strOrDefault(m, "description", ""),
		Action:          action,
		ActionParams:    mapOrEmpty(m, "action_params"),
		HIPAAControls:   strSlice(m, "hipaa_controls"),
		SeverityFilter:  strSlice(m, "severity_filter"),
		Enabled:         boolOrDefault(m, "enabled", true),
		Priority:        intOrDefault(m, "priority", 5), // Synced rules default to priority 5
		CooldownSeconds: intOrDefault(m, "cooldown_seconds", 300),
		MaxRetries:      intOrDefault(m, "max_retries", 1),
		Source:          "synced",
		GPOManaged:      boolOrDefault(m, "gpo_managed", false),
	}

	if conds, ok := m["conditions"].([]interface{}); ok {
		for _, c := range conds {
			if cm, ok := c.(map[string]interface{}); ok {
				r.Conditions = append(r.Conditions, RuleCondition{
					Field:    strOrDefault(cm, "field", ""),
					Operator: MatchOperator(strOrDefault(cm, "operator", "eq")),
					Value:    cm["value"],
				})
			}
		}
	}

	return r
}

// --- JSON helpers ---

// jsonMarshalSorted produces JSON with sorted keys, matching Python's json.dumps(obj, sort_keys=True).
// This is needed for deterministic signature verification.
func jsonMarshalSorted(v interface{}) ([]byte, error) {
	switch val := v.(type) {
	case map[string]interface{}:
		keys := make([]string, 0, len(val))
		for k := range val {
			keys = append(keys, k)
		}
		sort.Strings(keys)

		buf := []byte("{")
		for i, k := range keys {
			if i > 0 {
				buf = append(buf, ',', ' ')
			}
			kJSON, _ := json.Marshal(k)
			buf = append(buf, kJSON...)
			buf = append(buf, ':', ' ')
			vJSON, err := jsonMarshalSorted(val[k])
			if err != nil {
				return nil, err
			}
			buf = append(buf, vJSON...)
		}
		buf = append(buf, '}')
		return buf, nil

	case []interface{}:
		buf := []byte("[")
		for i, item := range val {
			if i > 0 {
				buf = append(buf, ',', ' ')
			}
			itemJSON, err := jsonMarshalSorted(item)
			if err != nil {
				return nil, err
			}
			buf = append(buf, itemJSON...)
		}
		buf = append(buf, ']')
		return buf, nil

	default:
		return json.Marshal(v)
	}
}

// --- Map access helpers ---

func strOrDefault(m map[string]interface{}, key, def string) string {
	if v, ok := m[key].(string); ok {
		return v
	}
	return def
}

func intOrDefault(m map[string]interface{}, key string, def int) int {
	switch v := m[key].(type) {
	case int:
		return v
	case float64:
		return int(v)
	case int64:
		return int(v)
	}
	return def
}

func boolOrDefault(m map[string]interface{}, key string, def bool) bool {
	if v, ok := m[key].(bool); ok {
		return v
	}
	return def
}

func mapOrEmpty(m map[string]interface{}, key string) map[string]interface{} {
	if v, ok := m[key].(map[string]interface{}); ok {
		return v
	}
	return map[string]interface{}{}
}

func strSlice(m map[string]interface{}, key string) []string {
	raw, ok := m[key].([]interface{})
	if !ok {
		return nil
	}
	result := make([]string, 0, len(raw))
	for _, v := range raw {
		if s, ok := v.(string); ok {
			result = append(result, s)
		}
	}
	return result
}
