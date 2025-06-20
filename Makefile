.PHONY: test test-verbose install-test-deps clean help all

install-test-deps:
	@echo "Installing test dependencies..."
	pip install .[test]

test:
	@echo "Running integration tests..."
	python -m tests.test_runner

test-verbose:
	@echo "Running integration tests (verbose mode)..."
	python -m tests.test_runner -v

clean:
	@echo "Cleaning temporary files..."
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.log" -delete
	rm -f test_config_*.json

help:
	@echo "Proxy Load Balancer Commands:"
	@echo ""
	@echo "  make install-test-deps - Install test dependencies"
	@echo "  make test              - Run integration tests"  
	@echo "  make test-verbose      - Run integration tests with verbose output"
	@echo "  make clean             - Clean temporary files"
	@echo "  make help              - Show this help"

all: test
