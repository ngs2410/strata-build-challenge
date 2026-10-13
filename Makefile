.PHONY: install test test-cov lint format clean eval eval-report review

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

eval:
	env3.12/bin/python -m scripts.main_eval

# Runs the eval and writes a dated markdown report at
# docs/eval-results/eval-results-<YYYY-MM-DD>.md intended to be committed
# alongside the submission so trust-metric numbers are reproducible from the
# repo without re-running the eval.
eval-report:
	env3.12/bin/python -m scripts.main_eval \
		--md_output=docs/eval-results/eval-results-$$(date -u +%Y-%m-%d).md

review:
	env3.12/bin/python -m scripts.main_review
