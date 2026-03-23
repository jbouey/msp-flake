.PHONY: proto build build-linux build-windows test vet lint clean

PROTO_DIR := ../proto
OUT_DIR := proto
VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo "dev")
LDFLAGS := -s -w -X github.com/osiriscare/appliance/internal/daemon.Version=$(VERSION)

proto:
	protoc \
		--go_out=$(OUT_DIR) --go_opt=paths=source_relative \
		--go-grpc_out=$(OUT_DIR) --go-grpc_opt=paths=source_relative \
		--proto_path=$(PROTO_DIR) \
		$(PROTO_DIR)/compliance.proto

build: proto
	go build -ldflags="$(LDFLAGS)" ./cmd/grpc-server/
	go build -ldflags="$(LDFLAGS)" ./cmd/appliance-daemon/
	go build -ldflags="$(LDFLAGS)" ./cmd/checkin-receiver/

build-linux:
	GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build \
		-ldflags="$(LDFLAGS)" \
		-o bin/appliance-daemon ./cmd/appliance-daemon/

build-windows:
	GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build \
		-ldflags="$(LDFLAGS)" \
		-o bin/appliance-daemon.exe ./cmd/appliance-daemon/

test:
	go test ./... -v -count=1

vet:
	go vet ./...

lint:
	golangci-lint run ./...

clean:
	rm -f $(OUT_DIR)/*.pb.go
	rm -rf bin/
	go clean ./...
