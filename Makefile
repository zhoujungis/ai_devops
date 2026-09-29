# Cross-platform entry points. Windows users without `make` can run the
# underlying commands directly (see README).

VENV ?= backend/.venv
CLI_VENV ?= cli/.venv

ifeq ($(OS),Windows_NT)
	PY := $(VENV)/Scripts/python.exe
	CLI_PY := $(CLI_VENV)/Scripts/python.exe
else
	PY := $(VENV)/bin/python
	CLI_PY := $(CLI_VENV)/bin/python
endif

BACKEND := backend
CLI := cli

.DEFAULT_GOAL := help
.PHONY: help venv install check migrate makemigrations test cov lint fmt typecheck verify run worker beat up down logs clean cli-venv cli-install cli-lint cli-typecheck cli-verify

help:
	@echo "venv          create the backend virtualenv"
	@echo "install       install backend dependencies (editable, with dev extras)"
	@echo "check         django system checks"
	@echo "migrate       apply migrations"
	@echo "makemigrations create migrations"
	@echo "test          run the test suite"
	@echo "cov           run tests with coverage report"
	@echo "lint          ruff"
	@echo "fmt           black + ruff --fix"
	@echo "typecheck     mypy"
	@echo "verify        lint + format check + typecheck + check + tests (CI equivalent)"
	@echo "run           run the dev server"
	@echo "worker        run a celery worker"
	@echo "beat          run celery beat"
	@echo "up / down     docker compose stack"
	@echo "logs          tail docker compose logs"
	@echo "cli-venv      create the terminal client virtualenv"
	@echo "cli-install   install the terminal client (editable, with dev extras)"
	@echo "cli-lint      ruff on the terminal client"
	@echo "cli-typecheck mypy on the terminal client"
	@echo "cli-verify    cli-lint + cli-typecheck"

venv:
	python -m venv $(VENV)

install:
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e "$(BACKEND)[dev]"

check:
	cd $(BACKEND) && ../$(PY) manage.py check

migrate:
	cd $(BACKEND) && ../$(PY) manage.py migrate

makemigrations:
	cd $(BACKEND) && ../$(PY) manage.py makemigrations

test:
	cd $(BACKEND) && ../$(PY) -m pytest

cov:
	cd $(BACKEND) && ../$(PY) -m pytest --cov --cov-report=term-missing

lint:
	cd $(BACKEND) && ../$(PY) -m ruff check .

fmt:
	cd $(BACKEND) && ../$(PY) -m black . && ../$(PY) -m ruff check --fix .

typecheck:
	cd $(BACKEND) && ../$(PY) -m mypy .

verify: lint typecheck check test

run:
	cd $(BACKEND) && ../$(PY) manage.py runserver

worker:
	cd $(BACKEND) && ../$(PY) -m celery -A config worker -Q ai,sync,default -l info

beat:
	cd $(BACKEND) && ../$(PY) -m celery -A config beat -l info

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

clean:
	rm -rf $(VENV) $(CLI_VENV) .pytest_cache .mypy_cache .ruff_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +

# ---------------------------------------------------------------------------
# Terminal client (cli/)
# ---------------------------------------------------------------------------
cli-venv:
	python -m venv $(CLI_VENV)

cli-install:
	$(CLI_PY) -m pip install --upgrade pip
	$(CLI_PY) -m pip install -e "$(CLI)[dev]"

cli-lint:
	cd $(CLI) && ../$(CLI_PY) -m ruff check .

cli-typecheck:
	cd $(CLI) && ../$(CLI_PY) -m mypy ai_devops_cli

cli-verify: cli-lint cli-typecheck
