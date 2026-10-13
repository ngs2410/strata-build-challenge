.PHONY: install test test-cov lint format clean

install:
	pip install -e '.[dev]'

test:
	pytest tests/

test-cov:
	pytest tests/ --cov=src --cov-report=term-missing

lint:
	ruff check src tests scripts

format:
	ruff format src tests scripts
	ruff check --fix src tests scripts

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .model_cache
