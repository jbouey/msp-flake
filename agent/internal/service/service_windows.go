//go:build windows

// Package service provides Windows Service Control Manager integration.
// This allows the agent to run as a proper Windows service with
// Start, Stop, Interrogate, and Shutdown support.
package service

import (
	"context"
	"log"
	"time"

	"golang.org/x/sys/windows/svc"
)

const ServiceName = "OsirisCareAgent"

// AgentService implements svc.Handler for the Windows Service Control Manager.
type AgentService struct {
	RunFunc func(ctx context.Context) error
}

// Execute is called by the Windows SCM. It manages the service lifecycle.
func (s *AgentService) Execute(args []string, r <-chan svc.ChangeRequest, changes chan<- svc.Status) (ssec bool, errno uint32) {
	const cmdsAccepted = svc.AcceptStop | svc.AcceptShutdown

	changes <- svc.Status{State: svc.StartPending}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	errCh := make(chan error, 1)
	go func() {
		errCh <- s.RunFunc(ctx)
	}()

	changes <- svc.Status{State: svc.Running, Accepts: cmdsAccepted}
	log.Println("[service] Windows service running")

	for {
		select {
		case c := <-r:
			switch c.Cmd {
			case svc.Interrogate:
				changes <- c.CurrentStatus
			case svc.Stop, svc.Shutdown:
				log.Printf("[service] SCM %v requested", c.Cmd)
				changes <- svc.Status{State: svc.StopPending}
				cancel()
				select {
				case <-errCh:
				case <-time.After(15 * time.Second):
					log.Println("[service] Graceful shutdown timed out after 15s")
				}
				return false, 0
			}
		case err := <-errCh:
			if err != nil {
				log.Printf("[service] Agent exited with error: %v", err)
				return false, 1
			}
			return false, 0
		}
	}
}

// IsWindowsService returns true if the process is running as a Windows service.
func IsWindowsService() bool {
	inService, err := svc.IsWindowsService()
	if err != nil {
		return false
	}
	return inService
}

// Run starts the agent as a Windows service under SCM control.
func Run(handler *AgentService) error {
	return svc.Run(ServiceName, handler)
}
