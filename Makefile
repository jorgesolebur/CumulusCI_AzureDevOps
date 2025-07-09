.PHONY: clean clean-test clean-pyc clean-build docs help
.DEFAULT_GOAL := help

define PRINT_HELP_PYSCRIPT
import re, sys

for line in sys.stdin:
	match = re.match(r'^([a-zA-Z_-]+):.*?## (.*)$$', line)
	if match:
		target, help = match.groups()
		print("%-20s %s" % (target, help))
endef
export PRINT_HELP_PYSCRIPT
BROWSER := python -c "$$BROWSER_PYSCRIPT"

help:
	@python -c "$$PRINT_HELP_PYSCRIPT" < $(MAKEFILE_LIST)

clean: clean-build clean-pyc clean-test ## remove all build, test, coverage and Python artifacts


clean-build: ## remove build artifacts
	rm -fr build/
	rm -fr pybuild/
	rm -fr dist/
	rm -fr .eggs/
	find . -name '*.egg-info' -exec rm -fr {} +
	find . -name '*.egg' -exec rm -f {} +

clean-pyc: ## remove Python file artifacts
	find . -name '*.pyc' -exec rm -f {} +
	find . -name '*.pyo' -exec rm -f {} +
	find . -name '*~' -exec rm -f {} +
	find . -name '__pycache__' -exec rm -fr {} +

clean-test: ## remove test and coverage artifacts
	rm -fr .tox/
	rm -f .coverage
	rm -fr htmlcov/
	rm -f output.xml
	rm -f report.html

lint: ## check style with flake8
	flake8 cumulusci tests

test: ## run tests quickly with the default Python
	pytest

test-all: ## run tests on every Python version with tox
	tox

release: clean ## package and upload a release
	python utility/pin_dependencies.py
	hatch build
	hatch publish

dist: clean ## builds source and wheel package
	hatch build
	ls -l dist

install: clean ## install the package to the active Python's site-packages
	python -m pip install .

tag: clean
	git tag -a -m 'version $$(hatch version)' v$$(hatch version)
	git push --follow-tags

update-deps:
	echo Use the _Update Python Dependencies_ Github action for real releases
	pip-compile --upgrade --resolver=backtracking --output-file=requirements/prod.txt pyproject.toml
	pip-compile --upgrade --resolver=backtracking --output-file=requirements/dev.txt --all-extras pyproject.toml

dev-install:
	python -m pip install --upgrade pip pip-tools setuptools
	pip-sync requirements/*.txt
	python -m pip install -e .

