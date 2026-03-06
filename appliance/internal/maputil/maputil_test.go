package maputil

import "testing"

func TestString(t *testing.T) {
	m := map[string]interface{}{
		"name": "test",
		"num":  42,
		"nil":  nil,
	}
	if got := String(m, "name"); got != "test" {
		t.Errorf("String(name) = %q, want %q", got, "test")
	}
	if got := String(m, "missing"); got != "" {
		t.Errorf("String(missing) = %q, want empty", got)
	}
	if got := String(m, "num"); got != "" {
		t.Errorf("String(num) = %q, want empty (wrong type)", got)
	}
	if got := String(m, "nil"); got != "" {
		t.Errorf("String(nil) = %q, want empty", got)
	}
}

func TestBool(t *testing.T) {
	m := map[string]interface{}{
		"flag": true,
		"str":  "yes",
	}
	if got := Bool(m, "flag"); !got {
		t.Error("Bool(flag) = false, want true")
	}
	if got := Bool(m, "missing"); got {
		t.Error("Bool(missing) = true, want false")
	}
	if got := Bool(m, "str"); got {
		t.Error("Bool(str) = true, want false (wrong type)")
	}
}

func TestMap(t *testing.T) {
	inner := map[string]interface{}{"k": "v"}
	m := map[string]interface{}{
		"sub": inner,
		"str": "not-a-map",
	}
	if got := Map(m, "sub"); got == nil || got["k"] != "v" {
		t.Errorf("Map(sub) = %v, want inner map", got)
	}
	if got := Map(m, "str"); got != nil {
		t.Errorf("Map(str) = %v, want nil", got)
	}
}

func TestSlice(t *testing.T) {
	m := map[string]interface{}{
		"items": []interface{}{"a", "b"},
		"str":   "not-a-slice",
	}
	if got := Slice(m, "items"); len(got) != 2 {
		t.Errorf("Slice(items) len = %d, want 2", len(got))
	}
	if got := Slice(m, "str"); got != nil {
		t.Errorf("Slice(str) = %v, want nil", got)
	}
}

func TestStringDefault(t *testing.T) {
	m := map[string]interface{}{"k": "val"}
	if got := StringDefault(m, "k", "def"); got != "val" {
		t.Errorf("StringDefault(k) = %q, want val", got)
	}
	if got := StringDefault(m, "missing", "def"); got != "def" {
		t.Errorf("StringDefault(missing) = %q, want def", got)
	}
}
