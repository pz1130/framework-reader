.PHONY: install test build clean

install:
	python -m pip install -e ".[dev]"

test:
	.venv/bin/python -m pytest -v

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
