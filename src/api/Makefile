GOLANGCI_LINT_VERSION := v2.15.0
GOLANGCI_LINT ?= go run github.com/golangci/golangci-lint/v2/cmd/golangci-lint@$(GOLANGCI_LINT_VERSION)

.PHONY: build tidy lint format format-check test check

build:
	go build -mod=readonly -trimpath -o bin/apple-remote-sign .

tidy:
	go mod tidy
	go mod verify

lint:
	$(GOLANGCI_LINT) run --build-tags testfixture

format:
	$(GOLANGCI_LINT) fmt

format-check:
	@test -z "$$(gofmt -l .)" || (gofmt -l . && exit 1)

test:
	go test -mod=readonly -tags testfixture -race ./...

check: build
