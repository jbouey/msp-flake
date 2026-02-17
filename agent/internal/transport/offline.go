// Package transport handles communication with the appliance.
package transport

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"strings"
	"sync"
	"time"

	_ "github.com/mattn/go-sqlite3"
	pb "github.com/osiriscare/agent/proto"
)

// Default queue limits
const (
	// DefaultMaxQueueSize is the maximum number of events to store
	DefaultMaxQueueSize = 10000
	// DefaultMaxQueueAge is the maximum age of events before pruning
	DefaultMaxQueueAge = 7 * 24 * time.Hour // 7 days
)

// ErrQueueFull is returned when the queue has reached its maximum size
var ErrQueueFull = fmt.Errorf("offline queue is full")

// OfflineQueue stores events when the appliance is unreachable.
// Uses SQLite with WAL mode for durability.
type OfflineQueue struct {
	db       *sql.DB
	mu       sync.Mutex
	maxSize  int
	maxAge   time.Duration
}

// QueuedEvent represents an event stored in the offline queue
type QueuedEvent struct {
	ID        int64
	EventType string // "drift", "heartbeat", "rmm"
	Payload   []byte
	CreatedAt time.Time
	Retries   int
}

// QueueOptions configures the offline queue
type QueueOptions struct {
	MaxSize int           // Maximum number of events (0 = use default)
	MaxAge  time.Duration // Maximum age before pruning (0 = use default)
}

// NewOfflineQueue creates a new offline queue backed by SQLite
func NewOfflineQueue(dataDir string) (*OfflineQueue, error) {
	return NewOfflineQueueWithOptions(dataDir, QueueOptions{})
}

// NewOfflineQueueWithOptions creates a new offline queue with custom options
func NewOfflineQueueWithOptions(dataDir string, opts QueueOptions) (*OfflineQueue, error) {
	dbPath := dataDir + "/offline_queue.db"

	// Apply defaults
	if opts.MaxSize <= 0 {
		opts.MaxSize = DefaultMaxQueueSize
	}
	if opts.MaxAge <= 0 {
		opts.MaxAge = DefaultMaxQueueAge
	}

	// Open database with WAL mode for better concurrent access
	db, err := sql.Open("sqlite3", dbPath+"?_journal_mode=WAL&_synchronous=NORMAL")
	if err != nil {
		return nil, fmt.Errorf("failed to open queue database: %w", err)
	}

	// Create table if not exists
	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS events (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			event_type TEXT NOT NULL,
			payload BLOB NOT NULL,
			created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
			retries INTEGER DEFAULT 0
		)
	`)
	if err != nil {
		db.Close()
		return nil, fmt.Errorf("failed to create events table: %w", err)
	}

	// Create index for efficient dequeue
	_, err = db.Exec(`
		CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at)
	`)
	if err != nil {
		db.Close()
		return nil, fmt.Errorf("failed to create index: %w", err)
	}

	return &OfflineQueue{
		db:      db,
		maxSize: opts.MaxSize,
		maxAge:  opts.MaxAge,
	}, nil
}

// Enqueue adds an event to the offline queue
func (q *OfflineQueue) Enqueue(event *pb.DriftEvent) error {
	q.mu.Lock()
	defer q.mu.Unlock()

	// Check queue size limit
	if err := q.enforceLimit(); err != nil {
		return err
	}

	payload, err := json.Marshal(event)
	if err != nil {
		return fmt.Errorf("failed to marshal event: %w", err)
	}

	_, err = q.db.Exec(
		"INSERT INTO events (event_type, payload) VALUES (?, ?)",
		"drift", payload,
	)
	if err != nil {
		return fmt.Errorf("failed to enqueue event: %w", err)
	}

	return nil
}

// EnqueueRaw adds a raw event to the queue
func (q *OfflineQueue) EnqueueRaw(eventType string, payload []byte) error {
	q.mu.Lock()
	defer q.mu.Unlock()

	// Check queue size limit
	if err := q.enforceLimit(); err != nil {
		return err
	}

	_, err := q.db.Exec(
		"INSERT INTO events (event_type, payload) VALUES (?, ?)",
		eventType, payload,
	)
	if err != nil {
		return fmt.Errorf("failed to enqueue event: %w", err)
	}

	return nil
}

// enforceLimit checks and enforces queue size limits.
// Must be called with mutex held.
func (q *OfflineQueue) enforceLimit() error {
	// First, prune old events
	cutoff := time.Now().Add(-q.maxAge)
	if _, err := q.db.Exec("DELETE FROM events WHERE created_at < ?", cutoff); err != nil {
		log.Printf("[OfflineQueue] Failed to prune old events: %v", err)
	}

	// Check count
	var count int
	row := q.db.QueryRow("SELECT COUNT(*) FROM events")
	if err := row.Scan(&count); err != nil {
		return fmt.Errorf("failed to count events: %w", err)
	}

	// If still at limit, remove oldest events to make room
	if count >= q.maxSize {
		// Delete oldest 10% to avoid repeated pruning
		toDelete := q.maxSize / 10
		if toDelete < 1 {
			toDelete = 1
		}
		_, err := q.db.Exec(`
			DELETE FROM events WHERE id IN (
				SELECT id FROM events ORDER BY created_at ASC LIMIT ?
			)
		`, toDelete)
		if err != nil {
			return fmt.Errorf("failed to prune queue: %w", err)
		}
	}

	return nil
}

// Dequeue retrieves and removes the oldest event from the queue
func (q *OfflineQueue) Dequeue() (*pb.DriftEvent, bool) {
	q.mu.Lock()
	defer q.mu.Unlock()

	// Get oldest event
	row := q.db.QueryRow(`
		SELECT id, payload FROM events
		WHERE event_type = 'drift'
		ORDER BY created_at ASC
		LIMIT 1
	`)

	var id int64
	var payload []byte
	if err := row.Scan(&id, &payload); err != nil {
		if err == sql.ErrNoRows {
			return nil, false
		}
		return nil, false
	}

	// Delete the event
	_, err := q.db.Exec("DELETE FROM events WHERE id = ?", id)
	if err != nil {
		return nil, false
	}

	// Unmarshal event
	var event pb.DriftEvent
	if err := json.Unmarshal(payload, &event); err != nil {
		return nil, false
	}

	return &event, true
}

// DequeueAll retrieves and removes all events up to a limit
func (q *OfflineQueue) DequeueAll(limit int) ([]*pb.DriftEvent, error) {
	q.mu.Lock()
	defer q.mu.Unlock()

	rows, err := q.db.Query(`
		SELECT id, payload FROM events
		WHERE event_type = 'drift'
		ORDER BY created_at ASC
		LIMIT ?
	`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var events []*pb.DriftEvent
	var ids []int64

	for rows.Next() {
		var id int64
		var payload []byte
		if err := rows.Scan(&id, &payload); err != nil {
			continue
		}

		var event pb.DriftEvent
		if err := json.Unmarshal(payload, &event); err != nil {
			continue
		}

		events = append(events, &event)
		ids = append(ids, id)
	}

	// Batch delete processed events
	if len(ids) > 0 {
		placeholders := make([]string, len(ids))
		args := make([]interface{}, len(ids))
		for i, id := range ids {
			placeholders[i] = "?"
			args[i] = id
		}
		query := fmt.Sprintf("DELETE FROM events WHERE id IN (%s)", strings.Join(placeholders, ","))
		if _, err := q.db.Exec(query, args...); err != nil {
			log.Printf("[OfflineQueue] Failed to delete %d dequeued events: %v", len(ids), err)
		}
	}

	return events, nil
}

// Count returns the number of queued events
func (q *OfflineQueue) Count() int {
	q.mu.Lock()
	defer q.mu.Unlock()

	var count int
	row := q.db.QueryRow("SELECT COUNT(*) FROM events")
	if err := row.Scan(&count); err != nil {
		return 0
	}
	return count
}

// Prune removes events older than the specified duration
func (q *OfflineQueue) Prune(maxAge time.Duration) (int, error) {
	q.mu.Lock()
	defer q.mu.Unlock()

	cutoff := time.Now().Add(-maxAge)
	result, err := q.db.Exec(
		"DELETE FROM events WHERE created_at < ?",
		cutoff,
	)
	if err != nil {
		return 0, err
	}

	affected, _ := result.RowsAffected()
	return int(affected), nil
}

// QueueStats contains queue status information
type QueueStats struct {
	Count      int           // Current number of events
	MaxSize    int           // Maximum allowed events
	MaxAge     time.Duration // Maximum age for events
	OldestAge  time.Duration // Age of oldest event (0 if empty)
	UsageRatio float64       // Current usage as ratio (0.0 to 1.0)
}

// Stats returns current queue statistics
func (q *OfflineQueue) Stats() QueueStats {
	q.mu.Lock()
	defer q.mu.Unlock()

	stats := QueueStats{
		MaxSize: q.maxSize,
		MaxAge:  q.maxAge,
	}

	// Get count
	row := q.db.QueryRow("SELECT COUNT(*) FROM events")
	if err := row.Scan(&stats.Count); err != nil {
		return stats
	}

	// Calculate usage ratio
	if q.maxSize > 0 {
		stats.UsageRatio = float64(stats.Count) / float64(q.maxSize)
	}

	// Get oldest event age
	var oldestTime time.Time
	row = q.db.QueryRow("SELECT created_at FROM events ORDER BY created_at ASC LIMIT 1")
	if err := row.Scan(&oldestTime); err == nil {
		stats.OldestAge = time.Since(oldestTime)
	}

	return stats
}

// Close closes the database connection
func (q *OfflineQueue) Close() error {
	return q.db.Close()
}
