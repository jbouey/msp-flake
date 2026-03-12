package logshipper

import (
	"testing"
)

func TestParseJournalTimestamp(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"1710000000000000", "2024-03-09T16:00:00Z"},
		{"1710000000500000", "2024-03-09T16:00:00.5Z"},
		{"", ""},
		{"abc", ""},
	}
	for _, tt := range tests {
		got := parseJournalTimestamp(tt.input)
		if got != tt.want {
			t.Errorf("parseJournalTimestamp(%q) = %q, want %q", tt.input, got, tt.want)
		}
	}
}

func TestParsePriority(t *testing.T) {
	tests := []struct {
		input string
		want  int
	}{
		{"0", 0},
		{"3", 3},
		{"7", 7},
		{"8", 6},  // out of range → default
		{"", 6},   // empty → default
		{"abc", 6},
	}
	for _, tt := range tests {
		got := parsePriority(tt.input)
		if got != tt.want {
			t.Errorf("parsePriority(%q) = %d, want %d", tt.input, got, tt.want)
		}
	}
}
