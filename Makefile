.PHONY: run install clean

install:
	pip install -r requirements.txt

run:
	python cmd/main.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
