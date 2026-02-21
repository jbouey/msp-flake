package l2planner

import (
	"testing"
)

func TestTruncate(t *testing.T) {
	if truncate("hello", 10) != "hello" {
		t.Error("Short string should not be truncated")
	}
	if truncate("hello world", 5) != "hello..." {
		t.Errorf("Long string truncation: got %q", truncate("hello world", 5))
	}
	if truncate("", 5) != "" {
		t.Error("Empty string should stay empty")
	}
}
