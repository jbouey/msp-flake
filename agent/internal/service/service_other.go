//go:build !windows

// Package service provides stubs for non-Windows systems.
package service

import "context"

const ServiceName = "OsirisCareAgent"

// AgentService is a no-op on non-Windows.
type AgentService struct {
	RunFunc func(ctx context.Context) error
}

// IsWindowsService always returns false on non-Windows.
func IsWindowsService() bool { return false }

// Run is a no-op on non-Windows.
func Run(handler *AgentService) error { return nil }
