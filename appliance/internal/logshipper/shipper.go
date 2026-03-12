// Package logshipper tails the local journald and ships batches to Central Command.
package logshipper

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// journalEntry maps to journalctl --output=json fields.
type journalEntry struct {
	Timestamp        string `json:"__REALTIME_TIMESTAMP"` // microseconds since epoch
	SystemdUnit      string `json:"_SYSTEMD_UNIT"`
	SyslogIdentifier string `json:"SYSLOG_IDENTIFIER"`
	Priority         string `json:"PRIORITY"`
	Message          string `json:"MESSAGE"`
	BootID           string `json:"_BOOT_ID"`
}

// logEntry is the wire format for Central Command's /api/logs/ingest.
type logEntry struct {
	TS   string `json:"ts"`
	Unit string `json:"unit"`
	Pri  int    `json:"pri"`
	Msg  string `json:"msg"`
	Boot string `json:"boot,omitempty"`
}

// Config for the log shipper.
type Config struct {
	APIEndpoint string // e.g. "https://api.osiriscare.net"
	APIKey      string
	SiteID      string
	Hostname    string
	StateDir    string        // persists cursor
	BatchSize   int           // max entries per POST (default 500)
	FlushEvery  time.Duration // how often to flush (default 30s)
	HTTPClient  *http.Client
}

// Shipper tails journald and ships log batches.
type Shipper struct {
	cfg        Config
	cursorFile string
	mu         sync.Mutex
}

// New creates a log shipper.
func New(cfg Config) *Shipper {
	if cfg.BatchSize <= 0 {
		cfg.BatchSize = 500
	}
	if cfg.FlushEvery <= 0 {
		cfg.FlushEvery = 30 * time.Second
	}
	if cfg.HTTPClient == nil {
		cfg.HTTPClient = &http.Client{Timeout: 30 * time.Second}
	}

	return &Shipper{
		cfg:        cfg,
		cursorFile: filepath.Join(cfg.StateDir, "logshipper-cursor"),
	}
}

// Run starts the shipper loop. Blocks until ctx is cancelled.
func (s *Shipper) Run(ctx context.Context) {
	log.Printf("[logshipper] Starting (endpoint=%s, batch=%d, flush=%s)",
		s.cfg.APIEndpoint, s.cfg.BatchSize, s.cfg.FlushEvery)

	ticker := time.NewTicker(s.cfg.FlushEvery)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			log.Println("[logshipper] Shutting down")
			return
		case <-ticker.C:
			if err := s.shipBatch(ctx); err != nil {
				log.Printf("[logshipper] Ship error: %v", err)
			}
		}
	}
}

// shipBatch reads new journal entries since last cursor, batches, and POSTs.
func (s *Shipper) shipBatch(ctx context.Context) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	entries, newCursor, err := s.readJournal(ctx)
	if err != nil {
		return fmt.Errorf("read journal: %w", err)
	}
	if len(entries) == 0 {
		return nil
	}

	if err := s.postBatch(ctx, entries); err != nil {
		return fmt.Errorf("post batch: %w", err)
	}

	// Persist cursor only after successful POST
	if newCursor != "" {
		if err := s.saveCursor(newCursor); err != nil {
			log.Printf("[logshipper] Warning: save cursor failed: %v", err)
		}
	}

	log.Printf("[logshipper] Shipped %d entries", len(entries))
	return nil
}

// readJournal runs journalctl to get new entries since cursor.
func (s *Shipper) readJournal(ctx context.Context) ([]logEntry, string, error) {
	args := []string{
		"--output=json",
		"--no-pager",
		fmt.Sprintf("--lines=%d", s.cfg.BatchSize),
	}

	// Use cursor for incremental reads
	cursor := s.loadCursor()
	if cursor != "" {
		args = append(args, "--after-cursor="+cursor)
	} else {
		// First run: only get last 5 minutes to avoid flooding
		args = append(args, "--since=-5m")
	}
	args = append(args, "--show-cursor")

	cmd := exec.CommandContext(ctx, "journalctl", args...)
	out, err := cmd.Output()
	if err != nil {
		// Exit code 1 with no output = no new entries
		if len(out) == 0 {
			return nil, "", nil
		}
		return nil, "", fmt.Errorf("journalctl: %w", err)
	}

	var entries []logEntry
	var lastCursor string

	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		// journalctl --show-cursor appends "-- cursor: <value>"
		if strings.HasPrefix(line, "-- cursor: ") {
			lastCursor = strings.TrimPrefix(line, "-- cursor: ")
			continue
		}

		var je journalEntry
		if err := json.Unmarshal([]byte(line), &je); err != nil {
			continue // skip malformed lines
		}

		// Parse timestamp (microseconds since epoch → ISO)
		ts := parseJournalTimestamp(je.Timestamp)
		if ts == "" {
			continue
		}

		unit := je.SystemdUnit
		if unit == "" {
			unit = je.SyslogIdentifier
		}
		if unit == "" {
			unit = "unknown"
		}

		pri := parsePriority(je.Priority)
		msg := je.Message
		if len(msg) > 8192 {
			msg = msg[:8192]
		}

		entries = append(entries, logEntry{
			TS:   ts,
			Unit: unit,
			Pri:  pri,
			Msg:  msg,
			Boot: je.BootID,
		})
	}

	return entries, lastCursor, nil
}

// postBatch sends a gzip-compressed JSON batch to Central Command.
func (s *Shipper) postBatch(ctx context.Context, entries []logEntry) error {
	payload := map[string]interface{}{
		"site_id":  s.cfg.SiteID,
		"hostname": s.cfg.Hostname,
		"batch":    entries,
	}

	jsonData, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("marshal: %w", err)
	}

	// Gzip compress
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	if _, err := gz.Write(jsonData); err != nil {
		return fmt.Errorf("gzip write: %w", err)
	}
	if err := gz.Close(); err != nil {
		return fmt.Errorf("gzip close: %w", err)
	}

	url := strings.TrimRight(s.cfg.APIEndpoint, "/") + "/api/logs/ingest"
	req, err := http.NewRequestWithContext(ctx, "POST", url, &buf)
	if err != nil {
		return fmt.Errorf("new request: %w", err)
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Content-Encoding", "gzip")
	req.Header.Set("Authorization", "Bearer "+s.cfg.APIKey)

	resp, err := s.cfg.HTTPClient.Do(req)
	if err != nil {
		return fmt.Errorf("HTTP POST: %w", err)
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body) //nolint:errcheck

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("server returned %d", resp.StatusCode)
	}

	return nil
}

func (s *Shipper) loadCursor() string {
	data, err := os.ReadFile(s.cursorFile)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}

func (s *Shipper) saveCursor(cursor string) error {
	return os.WriteFile(s.cursorFile, []byte(cursor), 0600)
}

// parseJournalTimestamp converts a __REALTIME_TIMESTAMP (microseconds since epoch) to ISO 8601.
func parseJournalTimestamp(usStr string) string {
	if usStr == "" {
		return ""
	}
	var us int64
	for _, c := range usStr {
		if c < '0' || c > '9' {
			return ""
		}
		us = us*10 + int64(c-'0')
	}
	sec := us / 1_000_000
	nsec := (us % 1_000_000) * 1000
	t := time.Unix(sec, nsec).UTC()
	return t.Format(time.RFC3339Nano)
}

// parsePriority parses syslog priority string to int (default 6=info).
func parsePriority(s string) int {
	if s == "" {
		return 6
	}
	v := 0
	for _, c := range s {
		if c < '0' || c > '9' {
			return 6
		}
		v = v*10 + int(c-'0')
	}
	if v > 7 {
		return 6
	}
	return v
}
