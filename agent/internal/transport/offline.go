// Package transport handles communication with the appliance.
package transport

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"os"
	"strings"
	"sync"
	"time"

	_ "modernc.org/sqlite"
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
// Payloads are encrypted at rest with AES-256-GCM when a cert key is available.
type OfflineQueue struct {
	db          *sql.DB
	mu          sync.Mutex
	maxSize     int
	maxAge      time.Duration
	certKeyPath string // path to agent TLS private key (for deriving encryption key)
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
	MaxSize     int           // Maximum number of events (0 = use default)
	MaxAge      time.Duration // Maximum age before pruning (0 = use default)
	CertKeyPath string        // Path to agent TLS key for payload encryption (optional)
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
	db, err := sql.Open("sqlite", dbPath+"?_journal_mode=WAL&_synchronous=NORMAL")
	if err != nil {
		return nil, fmt.Errorf("failed to open queue database: %w", err)
	}

	// Create table if not exists (encrypted column added for at-rest encryption)
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

	// Add encrypted column if it doesn't exist (migration for existing DBs)
	_, _ = db.Exec(`ALTER TABLE events ADD COLUMN encrypted INTEGER DEFAULT 0`)

	// Create index for efficient dequeue
	_, err = db.Exec(`
		CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at)
	`)
	if err != nil {
		db.Close()
		return nil, fmt.Errorf("failed to create index: %w", err)
	}

	return &OfflineQueue{
		db:          db,
		maxSize:     opts.MaxSize,
		maxAge:      opts.MaxAge,
		certKeyPath: opts.CertKeyPath,
	}, nil
}

// Enqueue adds an event to the offline queue.
// If a cert key is available, the payload is encrypted at rest with AES-256-GCM.
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

	encrypted := 0
	key, keyErr := deriveQueueKey(q.certKeyPath)
	if keyErr == nil {
		enc, encErr := encryptPayload(key, payload)
		if encErr == nil {
			payload = enc
			encrypted = 1
		} else {
			log.Printf("[OfflineQueue] WARNING: encryption failed, storing plaintext: %v", encErr)
		}
	}

	_, err = q.db.Exec(
		"INSERT INTO events (event_type, payload, encrypted) VALUES (?, ?, ?)",
		"drift", payload, encrypted,
	)
	if err != nil {
		return fmt.Errorf("failed to enqueue event: %w", err)
	}

	return nil
}

// EnqueueRaw adds a raw event to the queue.
// Encrypts at rest when a cert key is available.
func (q *OfflineQueue) EnqueueRaw(eventType string, payload []byte) error {
	q.mu.Lock()
	defer q.mu.Unlock()

	// Check queue size limit
	if err := q.enforceLimit(); err != nil {
		return err
	}

	encrypted := 0
	key, keyErr := deriveQueueKey(q.certKeyPath)
	if keyErr == nil {
		enc, encErr := encryptPayload(key, payload)
		if encErr == nil {
			payload = enc
			encrypted = 1
		}
	}

	_, err := q.db.Exec(
		"INSERT INTO events (event_type, payload, encrypted) VALUES (?, ?, ?)",
		eventType, payload, encrypted,
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

// Dequeue retrieves and removes the oldest event from the queue.
// Decrypts the payload if it was stored encrypted.
func (q *OfflineQueue) Dequeue() (*pb.DriftEvent, bool) {
	q.mu.Lock()
	defer q.mu.Unlock()

	// Get oldest event
	row := q.db.QueryRow(`
		SELECT id, payload, COALESCE(encrypted, 0) FROM events
		WHERE event_type = 'drift'
		ORDER BY created_at ASC
		LIMIT 1
	`)

	var id int64
	var payload []byte
	var encrypted int
	if err := row.Scan(&id, &payload, &encrypted); err != nil {
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

	// Decrypt if needed
	if encrypted == 1 {
		key, keyErr := deriveQueueKey(q.certKeyPath)
		if keyErr != nil {
			log.Printf("[OfflineQueue] Cannot decrypt event %d: cert key unavailable: %v", id, keyErr)
			return nil, false
		}
		dec, decErr := decryptPayload(key, payload)
		if decErr != nil {
			log.Printf("[OfflineQueue] Cannot decrypt event %d: %v", id, decErr)
			return nil, false
		}
		payload = dec
	}

	// Unmarshal event
	var event pb.DriftEvent
	if err := json.Unmarshal(payload, &event); err != nil {
		return nil, false
	}

	return &event, true
}

// DequeueAll retrieves and removes all events up to a limit.
// Decrypts encrypted payloads before returning.
func (q *OfflineQueue) DequeueAll(limit int) ([]*pb.DriftEvent, error) {
	q.mu.Lock()
	defer q.mu.Unlock()

	rows, err := q.db.Query(`
		SELECT id, payload, COALESCE(encrypted, 0) FROM events
		WHERE event_type = 'drift'
		ORDER BY created_at ASC
		LIMIT ?
	`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	// Pre-derive key once for decryption (may be nil if cert not yet available)
	queueKey, _ := deriveQueueKey(q.certKeyPath)

	var events []*pb.DriftEvent
	var ids []int64

	for rows.Next() {
		var id int64
		var payload []byte
		var encrypted int
		if err := rows.Scan(&id, &payload, &encrypted); err != nil {
			continue
		}

		if encrypted == 1 {
			if queueKey == nil {
				log.Printf("[OfflineQueue] Skipping encrypted event %d: no key available", id)
				continue
			}
			dec, decErr := decryptPayload(queueKey, payload)
			if decErr != nil {
				log.Printf("[OfflineQueue] Skipping event %d: decrypt failed: %v", id, decErr)
				continue
			}
			payload = dec
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

// SetCertKeyPath updates the cert key path after enrollment completes.
// This lets the queue start encrypting new events once the agent has certs.
func (q *OfflineQueue) SetCertKeyPath(path string) {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.certKeyPath = path
}

// UpgradePlaintextEvents re-encrypts any plaintext events in the queue.
// Call after enrollment when a cert key becomes available.
func (q *OfflineQueue) UpgradePlaintextEvents() int {
	q.mu.Lock()
	defer q.mu.Unlock()

	key, err := deriveQueueKey(q.certKeyPath)
	if err != nil {
		return 0
	}

	rows, err := q.db.Query(`SELECT id, payload FROM events WHERE COALESCE(encrypted, 0) = 0`)
	if err != nil {
		return 0
	}
	defer rows.Close()

	upgraded := 0
	for rows.Next() {
		var id int64
		var payload []byte
		if err := rows.Scan(&id, &payload); err != nil {
			continue
		}
		enc, err := encryptPayload(key, payload)
		if err != nil {
			continue
		}
		if _, err := q.db.Exec(`UPDATE events SET payload = ?, encrypted = 1 WHERE id = ?`, enc, id); err != nil {
			continue
		}
		upgraded++
	}

	if upgraded > 0 {
		log.Printf("[OfflineQueue] Upgraded %d plaintext events to encrypted", upgraded)
	}
	return upgraded
}

// Close closes the database connection
func (q *OfflineQueue) Close() error {
	return q.db.Close()
}

// --- AES-256-GCM payload encryption ---

// deriveQueueKey derives a 32-byte AES key from the agent's TLS private key file.
func deriveQueueKey(certKeyPath string) ([]byte, error) {
	if certKeyPath == "" {
		return nil, fmt.Errorf("no cert key path configured")
	}
	keyData, err := os.ReadFile(certKeyPath)
	if err != nil {
		return nil, err
	}
	hash := sha256.Sum256(keyData)
	return hash[:], nil
}

// encryptPayload encrypts plaintext with AES-256-GCM.
// Returns nonce || ciphertext.
func encryptPayload(key, plaintext []byte) ([]byte, error) {
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, gcm.NonceSize())
	if _, err := io.ReadFull(rand.Reader, nonce); err != nil {
		return nil, err
	}
	return gcm.Seal(nonce, nonce, plaintext, nil), nil
}

// decryptPayload decrypts an AES-256-GCM payload (nonce || ciphertext).
func decryptPayload(key, ciphertext []byte) ([]byte, error) {
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	nonceSize := gcm.NonceSize()
	if len(ciphertext) < nonceSize {
		return nil, fmt.Errorf("ciphertext too short")
	}
	return gcm.Open(nil, ciphertext[:nonceSize], ciphertext[nonceSize:], nil)
}
