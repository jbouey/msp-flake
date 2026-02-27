# Go Expert Patterns

## Concurrency

### errgroup (bounded parallelism)
```go
import "golang.org/x/sync/errgroup"

g, ctx := errgroup.WithContext(ctx)
g.SetLimit(10) // bounded concurrency

for _, item := range items {
    g.Go(func() error {
        return process(ctx, item) // ctx cancelled on first error
    })
}
if err := g.Wait(); err != nil {
    return fmt.Errorf("processing failed: %w", err)
}
```

### Fan-out / Fan-in Pipeline
```go
func pipeline(ctx context.Context, input <-chan Item) <-chan Result {
    out := make(chan Result)
    var wg sync.WaitGroup
    numWorkers := runtime.GOMAXPROCS(0)

    for range numWorkers {
        wg.Add(1)
        go func() {
            defer wg.Done()
            for item := range input {
                select {
                case <-ctx.Done():
                    return
                case out <- transform(item):
                }
            }
        }()
    }

    go func() {
        wg.Wait()
        close(out)
    }()
    return out
}
```

### Graceful Shutdown
```go
ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
defer stop()

srv := &http.Server{Addr: ":8080", Handler: mux}
go func() { srv.ListenAndServe() }()

<-ctx.Done()
shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
defer cancel()
srv.Shutdown(shutdownCtx)
```

### Context Patterns
```go
// WithoutCancel (Go 1.21+) — derived context that survives parent cancellation
cleanupCtx := context.WithoutCancel(ctx)

// AfterFunc (Go 1.21+) — callback when context done
stop := context.AfterFunc(ctx, func() {
    conn.Close()
})
defer stop()

// WithValue — use typed keys, not strings
type ctxKey struct{}
ctx = context.WithValue(ctx, ctxKey{}, value)
v := ctx.Value(ctxKey{}).(Type)
```

### Semaphore
```go
import "golang.org/x/sync/semaphore"

sem := semaphore.NewWeighted(10)
for _, item := range items {
    if err := sem.Acquire(ctx, 1); err != nil {
        break
    }
    go func() {
        defer sem.Release(1)
        process(item)
    }()
}
sem.Acquire(ctx, 10) // wait for all
```

## Memory & Performance

### Escape Analysis
```bash
go build -gcflags='-m -m' ./...  # Show escape analysis decisions
```

### sync.Pool (reduce GC pressure)
```go
var bufPool = sync.Pool{
    New: func() any { return new(bytes.Buffer) },
}

func process(data []byte) {
    buf := bufPool.Get().(*bytes.Buffer)
    defer func() {
        buf.Reset()
        bufPool.Put(buf)
    }()
    buf.Write(data)
    // use buf...
}
```

### GOGC / GOMEMLIMIT (Go 1.19+)
```go
// Soft memory limit — GC runs more aggressively near limit
// Set via env: GOMEMLIMIT=256MiB
// Or programmatically:
debug.SetMemoryLimit(256 << 20)

// GOGC — target heap growth ratio (default 100 = double before GC)
// GOGC=50  → GC at 50% growth (more frequent, lower memory)
// GOGC=off → disable GC (use with GOMEMLIMIT for memory-only trigger)
```

### Bounds Check Elimination
```go
// Prove to compiler that index is in bounds:
_ = slice[i] // bounds check here
// subsequent accesses to slice[0:i] are unchecked

// Or use len check:
if i < len(slice) {
    v := slice[i] // no bounds check
}
```

### Benchmarks (Go 1.24+)
```go
func BenchmarkProcess(b *testing.B) {
    data := setupData()
    b.ReportAllocs()
    b.ResetTimer()
    for b.Loop() { // Go 1.24: replaces for i := 0; i < b.N; i++
        process(data)
    }
}
// Compare: benchstat old.txt new.txt
```

### pprof
```go
import _ "net/http/pprof"
go http.ListenAndServe("localhost:6060", nil)

// Collect: go tool pprof http://localhost:6060/debug/pprof/heap
// CPU:     go tool pprof http://localhost:6060/debug/pprof/profile?seconds=30
// Goroutine: go tool pprof http://localhost:6060/debug/pprof/goroutine
```

## Interface Design

### Accept Interfaces, Return Structs
```go
// Good: function accepts interface
func Store(w io.Writer, data []byte) error {
    _, err := w.Write(data)
    return err
}

// Good: return concrete type
func NewServer(cfg Config) *Server { return &Server{cfg: cfg} }
```

### Compile-Time Interface Check
```go
var _ io.ReadCloser = (*MyReader)(nil) // compile error if MyReader doesn't implement
```

### Small Interfaces
```go
// Prefer 1-2 method interfaces
type Validator interface {
    Validate() error
}

// Compose via embedding
type ReadValidator interface {
    io.Reader
    Validator
}
```

### Functional Options
```go
type Option func(*Server)

func WithPort(port int) Option  { return func(s *Server) { s.port = port } }
func WithTLS(cfg *tls.Config) Option { return func(s *Server) { s.tls = cfg } }

func NewServer(opts ...Option) *Server {
    s := &Server{port: 8080} // defaults
    for _, o := range opts {
        o(s)
    }
    return s
}
```

## Error Handling

### Sentinel Errors + Wrapping
```go
var (
    ErrNotFound   = errors.New("not found")
    ErrForbidden  = errors.New("forbidden")
)

// Wrap with context
return fmt.Errorf("loading site %s: %w", id, ErrNotFound)

// Check
if errors.Is(err, ErrNotFound) { /* handle */ }

// Extract typed error
var pathErr *os.PathError
if errors.As(err, &pathErr) { /* use pathErr.Path */ }
```

### Custom Error Types
```go
type ValidationError struct {
    Field   string
    Message string
}
func (e *ValidationError) Error() string {
    return fmt.Sprintf("validation: %s: %s", e.Field, e.Message)
}
// Check: errors.As(err, &ve)
```

### Never Ignore Errors
```go
// If you truly don't care, be explicit:
_ = conn.Close() // intentional: best-effort cleanup
```

## Testing

### Table-Driven Tests
```go
func TestParse(t *testing.T) {
    tests := []struct {
        name    string
        input   string
        want    int
        wantErr bool
    }{
        {"valid", "42", 42, false},
        {"empty", "", 0, true},
        {"negative", "-1", -1, false},
    }
    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            got, err := Parse(tt.input)
            if (err != nil) != tt.wantErr {
                t.Fatalf("Parse(%q) error = %v, wantErr %v", tt.input, err, tt.wantErr)
            }
            if got != tt.want {
                t.Errorf("Parse(%q) = %v, want %v", tt.input, got, tt.want)
            }
        })
    }
}
```

### t.Helper / t.Cleanup
```go
func setupDB(t *testing.T) *sql.DB {
    t.Helper() // errors report caller's line, not this function
    db, err := sql.Open("postgres", testDSN)
    if err != nil {
        t.Fatal(err)
    }
    t.Cleanup(func() { db.Close() }) // runs after test, even on failure
    return db
}
```

### Fuzzing
```go
func FuzzParseJSON(f *testing.F) {
    f.Add([]byte(`{"key":"value"}`))
    f.Add([]byte(`{}`))
    f.Fuzz(func(t *testing.T, data []byte) {
        var result map[string]any
        _ = json.Unmarshal(data, &result) // should never panic
    })
}
// Run: go test -fuzz=FuzzParseJSON -fuzztime=30s
```

### Golden Files
```go
func TestRender(t *testing.T) {
    got := render(input)
    golden := filepath.Join("testdata", t.Name()+".golden")
    if *update { // var update = flag.Bool("update", false, "update golden files")
        os.WriteFile(golden, []byte(got), 0644)
    }
    want, _ := os.ReadFile(golden)
    if diff := cmp.Diff(string(want), got); diff != "" {
        t.Errorf("mismatch (-want +got):\n%s", diff)
    }
}
```

### Goroutine Leak Detection
```go
import "go.uber.org/goleak"

func TestMain(m *testing.M) {
    goleak.VerifyTestMain(m)
}
```

### testcontainers-go
```go
func TestWithPostgres(t *testing.T) {
    ctx := context.Background()
    pg, err := postgres.Run(ctx, "postgres:16",
        postgres.WithDatabase("test"),
        postgres.WithUsername("test"),
        postgres.WithPassword("test"),
        testcontainers.WithWaitStrategy(
            wait.ForLog("database system is ready").WithStartupTimeout(30*time.Second),
        ),
    )
    t.Cleanup(func() { pg.Terminate(ctx) })
    connStr, _ := pg.ConnectionString(ctx, "sslmode=disable")
    // use connStr...
}
```

## Networking & HTTP

### Production http.Server
```go
srv := &http.Server{
    Addr:         ":8080",
    Handler:      mux,
    ReadTimeout:  5 * time.Second,
    WriteTimeout: 10 * time.Second,
    IdleTimeout:  120 * time.Second,
    MaxHeaderBytes: 1 << 20, // 1MB
}
```

### http.Client (reuse, don't create per-request)
```go
var client = &http.Client{
    Timeout: 30 * time.Second,
    Transport: &http.Transport{
        MaxIdleConns:        100,
        MaxIdleConnsPerHost: 10,
        IdleConnTimeout:     90 * time.Second,
        TLSHandshakeTimeout: 10 * time.Second,
    },
}
```

### Rate Limiting
```go
import "golang.org/x/time/rate"

limiter := rate.NewLimiter(rate.Every(time.Second/10), 5) // 10/s, burst 5
if err := limiter.Wait(ctx); err != nil {
    return err // context cancelled
}
```

### Circuit Breaker
```go
import "github.com/sony/gobreaker/v2"

cb := gobreaker.NewCircuitBreaker[[]byte](gobreaker.Settings{
    Name:        "api",
    MaxRequests: 3,                    // half-open: allow 3 probes
    Interval:    60 * time.Second,     // closed: reset counter interval
    Timeout:     30 * time.Second,     // open→half-open timeout
    ReadyToTrip: func(counts gobreaker.Counts) bool {
        return counts.ConsecutiveFailures > 5
    },
})
result, err := cb.Execute(func() ([]byte, error) {
    return callAPI()
})
```

## Database (pgx)

### Connection Pool
```go
import "github.com/jackc/pgx/v5/pgxpool"

config, _ := pgxpool.ParseConfig(databaseURL)
config.MaxConns = 10
config.MinConns = 2
config.MaxConnLifetime = time.Hour
config.MaxConnIdleTime = 30 * time.Minute
config.HealthCheckPeriod = time.Minute

pool, err := pgxpool.NewWithConfig(ctx, config)
defer pool.Close()
```

### Transaction Pattern
```go
func WithTx(ctx context.Context, pool *pgxpool.Pool, fn func(pgx.Tx) error) error {
    tx, err := pool.Begin(ctx)
    if err != nil {
        return err
    }
    defer tx.Rollback(ctx) // no-op after commit

    if err := fn(tx); err != nil {
        return err
    }
    return tx.Commit(ctx)
}
```

### Batch Queries
```go
batch := &pgx.Batch{}
batch.Queue("INSERT INTO events (type, data) VALUES ($1, $2)", "click", data1)
batch.Queue("INSERT INTO events (type, data) VALUES ($1, $2)", "view", data2)

br := pool.SendBatch(ctx, batch)
defer br.Close()
for range 2 {
    if _, err := br.Exec(); err != nil {
        return err
    }
}
```

## Security

### Crypto
```go
import "crypto/rand"

// Always crypto/rand, never math/rand for security
token := make([]byte, 32)
if _, err := rand.Read(token); err != nil {
    panic(err) // system entropy exhausted — catastrophic
}

// Constant-time comparison (prevents timing attacks)
import "crypto/subtle"
if subtle.ConstantTimeCompare([]byte(got), []byte(want)) != 1 {
    return ErrInvalidToken
}
```

### TLS Configuration
```go
tlsConfig := &tls.Config{
    MinVersion: tls.VersionTLS12,
    CipherSuites: []uint16{
        tls.TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384,
        tls.TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384,
        tls.TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256,
        tls.TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256,
    },
    CurvePreferences: []tls.CurveID{tls.X25519, tls.CurveP256},
}
```

### Input Validation
```go
// SQL: always parameterized
row := db.QueryRow("SELECT * FROM users WHERE id = $1", userID)

// Path traversal: clean + validate
cleaned := filepath.Clean(userPath)
if !strings.HasPrefix(cleaned, allowedBase) {
    return ErrForbidden
}

// Command injection: never shell out with user input in string
// Use exec.Command with separate args, never fmt.Sprintf into sh -c
cmd := exec.Command("git", "log", "--oneline", "-n", strconv.Itoa(n))
```

## Production Patterns

### Structured Logging (slog)
```go
import "log/slog"

logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
    Level: slog.LevelInfo,
}))
slog.SetDefault(logger)

slog.Info("order processed",
    slog.String("order_id", id),
    slog.Int("items", count),
    slog.Duration("elapsed", elapsed),
)

// Group + child logger
logger = logger.With(slog.String("service", "daemon"))
logger.Info("started") // includes service=daemon
```

### Health Endpoint
```go
mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
    if err := pool.Ping(r.Context()); err != nil {
        http.Error(w, "db unhealthy", http.StatusServiceUnavailable)
        return
    }
    w.WriteHeader(http.StatusOK)
    fmt.Fprintln(w, "ok")
})
```

### sd_notify Integration
```go
// Zero-CGO sd_notify (this project: internal/sdnotify/sdnotify.go)
func Ready()    { notify("READY=1") }
func Stopping() { notify("STOPPING=1") }
func Watchdog() { notify("WATCHDOG=1") }

func notify(state string) {
    addr := os.Getenv("NOTIFY_SOCKET")
    if addr == "" { return }
    conn, _ := net.Dial("unixgram", addr)
    defer conn.Close()
    conn.Write([]byte(state))
}
```

### Signal Handling
```go
sigCh := make(chan os.Signal, 1)
signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM, syscall.SIGHUP)

go func() {
    for sig := range sigCh {
        switch sig {
        case syscall.SIGHUP:
            reloadConfig()
        case syscall.SIGINT, syscall.SIGTERM:
            shutdown()
        }
    }
}()
```

### File Locking (flock)
```go
import "syscall"

f, _ := os.OpenFile(lockPath, os.O_CREATE|os.O_RDWR, 0600)
if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
    return fmt.Errorf("another instance is running")
}
defer func() {
    syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
    f.Close()
}()
```

### Atomic File Write
```go
func atomicWrite(path string, data []byte) error {
    tmp := path + ".tmp"
    if err := os.WriteFile(tmp, data, 0644); err != nil {
        return err
    }
    return os.Rename(tmp, path) // atomic on same filesystem
}
```

## Generics (Go 1.18+)

### Type Constraints
```go
type Number interface {
    ~int | ~int64 | ~float64
}

func Sum[T Number](vals []T) T {
    var total T
    for _, v := range vals {
        total += v
    }
    return total
}
```

### Generic Map/Filter
```go
func Map[T, U any](s []T, f func(T) U) []U {
    result := make([]U, len(s))
    for i, v := range s {
        result[i] = f(v)
    }
    return result
}

func Filter[T any](s []T, f func(T) bool) []T {
    var result []T
    for _, v := range s {
        if f(v) { result = append(result, v) }
    }
    return result
}
```

### When to Use Generics vs Interfaces
```
Generics: collections, algorithms, type-safe containers, reducing boilerplate
Interfaces: behavior polymorphism, dependency injection, mocking
Rule: if the function body uses methods on the type → interface
       if the function body treats values opaquely → generic
```

## Anti-Patterns

```
AVOID                              USE INSTEAD
─────────────────────────────────  ─────────────────────────────────
unbounded goroutines               errgroup.SetLimit / semaphore
time.Sleep for sync                channels / sync.WaitGroup
goroutine leak (no done signal)    context cancellation / goleak
silent error: if err != nil {}     handle or explicitly _ = f()
god interface (10+ methods)        small interfaces, compose
init() side effects                explicit initialization
global mutable state               dependency injection
utils/helpers package              colocate with usage
panic in library code              return error
string concatenation in loop       strings.Builder
naked return in complex func       explicit returns
```

## Project-Specific (MSP Appliance Daemon)

### Key Packages
```
appliance/internal/daemon/     — main loop, checkin, scanners, runbooks
appliance/internal/orders/     — fleet order processing, Ed25519 verification
appliance/internal/healing/    — L1 deterministic rules engine
appliance/internal/l2planner/  — PHI scrubbing, LLM bridge, guardrails
appliance/internal/sshexec/    — SSH command execution
appliance/internal/winrm/      — WinRM (NTLM auth) execution
appliance/internal/crypto/     — Ed25519 signature verification
appliance/internal/sdnotify/   — Zero-CGO systemd notification
appliance/internal/ca/         — Certificate authority
appliance/internal/checkin/    — Checkin protocol + models
```

### Build & Test
```bash
cd appliance
go build ./...
go test ./...                           # all tests
go test ./internal/orders/ -v           # specific package
go test -race ./...                     # race detector
go test -fuzz=FuzzParseOrder ./internal/orders/ -fuzztime=30s
```

### Go Version Compatibility
- NixOS 24.05 ships Go 1.22 — do not use Go 1.23+ features (iterators, range-over-func)
- `b.Loop()` requires Go 1.24+ — use `for i := 0; i < b.N; i++` for now
