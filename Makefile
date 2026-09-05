.PHONY: install test build clean

install:
	python -m pip install -c constraints.txt -e ".[dev]"

test:
	.venv/bin/python -m pytest -v

.PHONY: check

check:
	.venv/bin/python -m ruff check src tests
	.venv/bin/python -m mypy src/framework_reader/web/uploads.py src/framework_reader/web/images.py src/framework_reader/schema/entities.py

build:
	python -m framework_reader.pack.build

clean:
	rm -rf build/ dist/ *.sqlite

.PHONY: sample

sample:
	fr sample-derived --n 30 --seed 42 --out build/r7_sample.csv

.PHONY: draft interview

draft:
	fr draft --all

interview:
	fr interview --next
