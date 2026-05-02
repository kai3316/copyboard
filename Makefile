.PHONY: run install test lint format clean

install:
	pip install -e ".[dev]"

run:
	python src/main.py

test:
	python -m pytest tests/ -v

lint:
	ruff check .

format:
	ruff format .

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
