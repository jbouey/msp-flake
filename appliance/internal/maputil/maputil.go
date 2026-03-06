// Package maputil provides typed extraction from map[string]interface{} values
// (JSON-decoded data). All functions return a zero value on missing/wrong-type keys
// and log a warning when a non-empty key has a type mismatch.
package maputil

import "log"

// String extracts a string from m[key]. Returns "" on missing or wrong type.
func String(m map[string]interface{}, key string) string {
	v, ok := m[key]
	if !ok || v == nil {
		return ""
	}
	s, ok := v.(string)
	if !ok {
		log.Printf("[maputil] key %q: expected string, got %T", key, v)
		return ""
	}
	return s
}

// Bool extracts a bool from m[key]. Returns false on missing or wrong type.
func Bool(m map[string]interface{}, key string) bool {
	v, ok := m[key]
	if !ok || v == nil {
		return false
	}
	b, ok := v.(bool)
	if !ok {
		log.Printf("[maputil] key %q: expected bool, got %T", key, v)
		return false
	}
	return b
}

// Map extracts a map[string]interface{} from m[key]. Returns nil on missing or wrong type.
func Map(m map[string]interface{}, key string) map[string]interface{} {
	v, ok := m[key]
	if !ok || v == nil {
		return nil
	}
	sub, ok := v.(map[string]interface{})
	if !ok {
		log.Printf("[maputil] key %q: expected map, got %T", key, v)
		return nil
	}
	return sub
}

// Slice extracts a []interface{} from m[key]. Returns nil on missing or wrong type.
func Slice(m map[string]interface{}, key string) []interface{} {
	v, ok := m[key]
	if !ok || v == nil {
		return nil
	}
	s, ok := v.([]interface{})
	if !ok {
		log.Printf("[maputil] key %q: expected slice, got %T", key, v)
		return nil
	}
	return s
}

// StringDefault extracts a string with a fallback default.
func StringDefault(m map[string]interface{}, key, def string) string {
	s := String(m, key)
	if s == "" {
		return def
	}
	return s
}
