.PHONY: build check install release

build:
	bash scripts/build.sh

check: build
	bash scripts/check.sh

install: build
	"$$(brew --prefix python@3.15)/bin/python3.15" scripts/service.py install

release:
	"$$(brew --prefix python@3.15)/bin/python3.15" scripts/package.py
