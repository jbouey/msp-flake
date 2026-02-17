.PHONY: all proto build-linux build-windows clean test fmt vet

VERSION := 0.3.0
BUILD_TIME := $(shell date -u +"%Y-%m-%dT%H:%M:%SZ")
LDFLAGS := -ldflags "-s -w -X main.Version=$(VERSION) -X main.BuildTime=$(BUILD_TIME)"

all: fmt vet build-linux build-windows

# Generate protobuf code (requires protoc and plugins)
proto:
	protoc --go_out=. --go_opt=paths=source_relative \
		--go-grpc_out=. --go-grpc_opt=paths=source_relative \
		proto/compliance.proto

# Build for Linux (development/testing)
build-linux:
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
		go build $(LDFLAGS) -o bin/osiris-agent-linux ./cmd/osiris-agent

# Build for Windows amd64 (cross-compile)
build-windows:
	CGO_ENABLED=1 GOOS=windows GOARCH=amd64 CC=x86_64-w64-mingw32-gcc \
		go build $(LDFLAGS) -o bin/osiris-agent.exe ./cmd/osiris-agent

# Build for Windows without CGO (limited WMI support)
build-windows-nocgo:
	CGO_ENABLED=0 GOOS=windows GOARCH=amd64 \
		go build $(LDFLAGS) -o bin/osiris-agent-nocgo.exe ./cmd/osiris-agent

# Build via Nix (reproducible)
nix-build:
	nix build .#osiris-agent-windows-amd64
	cp result/bin/osiris-agent.exe bin/

# Run tests
test:
	go test -v ./...

# Run tests with coverage
test-cover:
	go test -v -coverprofile=coverage.out ./...
	go tool cover -html=coverage.out -o coverage.html

# Format code
fmt:
	go fmt ./...

# Run vet
vet:
	go vet ./...

# Clean build artifacts
clean:
	rm -rf bin/
	rm -f proto/*.pb.go
	rm -f coverage.out coverage.html

# Install development dependencies
deps:
	go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
	go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest

# Run the agent locally (dry-run mode)
run-dry:
	go run ./cmd/osiris-agent -dry-run

# Create release tarball
release: build-windows
	mkdir -p release
	cp bin/osiris-agent.exe release/
	cp README.md release/ 2>/dev/null || true
	cd release && zip -r ../osiris-agent-$(VERSION)-windows-amd64.zip .
	rm -rf release

# Show help
help:
	@echo "OsirisCare Go Agent Build Commands"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  all            Build for Linux and Windows"
	@echo "  build-linux    Build for Linux amd64"
	@echo "  build-windows  Build for Windows amd64 (requires mingw-w64)"
	@echo "  test           Run tests"
	@echo "  test-cover     Run tests with coverage report"
	@echo "  fmt            Format code"
	@echo "  vet            Run go vet"
	@echo "  clean          Remove build artifacts"
	@echo "  deps           Install protobuf dependencies"
	@echo "  proto          Generate protobuf code"
	@echo "  run-dry        Run agent in dry-run mode"
	@echo "  release        Create release zip"
	@echo "  help           Show this help"
