// Package transport handles communication with the appliance.
package transport

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"sync"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// OfflineQueue stores events when the appliance is unreachable.
// Uses SQLite with WAL mode for durability.
type OfflineQueue struct {
	db *sql.DB
	mu sync.Mutex
}

// QueuedEvent represents an event stored in the offline queue
type QueuedEvent struct {
	ID        int64
	EventType string // "drift", "heartbeat", "rmm"
	Payload   []byte
	CreatedAt time.Time
	Retries   int
}

// NewOfflineQueue creates a new offline queue backed by SQLite
func NewOfflineQueue(dataDir string) (*OfflineQueue, error) {
	dbPath := dataDir + "/offline_queue.db"

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

	return &OfflineQueue{db: db}, nil
}

// Enqueue adds an event to the offline queue
func (q *OfflineQueue) Enqueue(event *DriftEvent) error {
	q.mu.Lock()
	defer q.mu.Unlock()

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

	_, err := q.db.Exec(
		"INSERT INTO events (event_type, payload) VALUES (?, ?)",
		eventType, payload,
	)
	if err != nil {
		return fmt.Errorf("failed to enqueue event: %w", err)
	}

	return nil
}

// Dequeue retrieves and removes the oldest event from the queue
func (q *OfflineQueue) Dequeue() (*DriftEvent, bool) {
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
	var event DriftEvent
	if err := json.Unmarshal(payload, &event); err != nil {
		return nil, false
	}

	return &event, true
}

// DequeueAll retrieves and removes all events up to a limit
func (q *OfflineQueue) DequeueAll(limit int) ([]*DriftEvent, error) {
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

	var events []*DriftEvent
	var ids []int64

	for rows.Next() {
		var id int64
		var payload []byte
		if err := rows.Scan(&id, &payload); err != nil {
			continue
		}

		var event DriftEvent
		if err := json.Unmarshal(payload, &event); err != nil {
			continue
		}

		events = append(events, &event)
		ids = append(ids, id)
	}

	// Delete processed events
	for _, id := range ids {
		q.db.Exec("DELETE FROM events WHERE id = ?", id)
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

// Close closes the database connection
func (q *OfflineQueue) Close() error {
	return q.db.Close()
}
