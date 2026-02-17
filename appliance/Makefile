.PHONY: proto build test clean

PROTO_DIR := ../proto
OUT_DIR := proto

proto:
	protoc \
		--go_out=$(OUT_DIR) --go_opt=paths=source_relative \
		--go-grpc_out=$(OUT_DIR) --go-grpc_opt=paths=source_relative \
		--proto_path=$(PROTO_DIR) \
		$(PROTO_DIR)/compliance.proto

build: proto
	go build ./cmd/grpc-server/
	go build ./cmd/appliance-daemon/
	go build ./cmd/checkin-receiver/

test:
	go test ./... -v -count=1

clean:
	rm -f $(OUT_DIR)/*.pb.go
	go clean ./...
