package evidence

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestCacheBundle(t *testing.T) {
	dir := t.TempDir()
	s := &Submitter{CacheDir: dir}

	body := []byte(`{"site_id":"test","checks":[]}`)
	if err := s.cacheBundle(body); err != nil {
		t.Fatalf("cacheBundle: %v", err)
	}

	entries, _ := os.ReadDir(dir)
	if len(entries) != 1 {
		t.Fatalf("expected 1 cached file, got %d", len(entries))
	}

	data, _ := os.ReadFile(filepath.Join(dir, entries[0].Name()))
	if string(data) != string(body) {
		t.Error("cached content doesn't match")
	}
}

func TestCacheBundleMaxLimit(t *testing.T) {
	dir := t.TempDir()
	s := &Submitter{CacheDir: dir}

	// Fill to 1000
	for i := 0; i < 1001; i++ {
		_ = s.cacheBundle([]byte(`{}`))
	}

	entries, _ := os.ReadDir(dir)
	if len(entries) > 1000 {
		t.Errorf("expected max 1000, got %d", len(entries))
	}
}

func TestCacheBundleNoCacheDir(t *testing.T) {
	s := &Submitter{} // no CacheDir
	if err := s.cacheBundle([]byte(`{}`)); err != nil {
		t.Error("should be no-op without CacheDir")
	}
}

func TestDrainCacheEmpty(t *testing.T) {
	dir := t.TempDir()
	s := &Submitter{CacheDir: dir, apiEndpoint: "http://localhost:99999"}

	n := s.DrainCache(context.Background())
	if n != 0 {
		t.Errorf("expected 0 drained from empty cache, got %d", n)
	}
}

func TestDrainCacheNoCacheDir(t *testing.T) {
	s := &Submitter{}
	n := s.DrainCache(context.Background())
	if n != 0 {
		t.Errorf("expected 0 without CacheDir, got %d", n)
	}
}
