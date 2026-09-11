.PHONY: help install dev api worker migrate revision test lint fmt client docker-up docker-down

VENV := ./.venv/bin

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Bog'liqliklarni o'rnatish (dev bilan)
	python3 -m venv .venv
	$(VENV)/pip install -U pip
	$(VENV)/pip install -r requirements-dev.txt

api:  ## API serverni ishga tushirish (reload bilan)
	$(VENV)/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

worker:  ## Fon workerni ishga tushirish
	$(VENV)/arq app.workers.worker.WorkerSettings

migrate:  ## Migratsiyalarni qo'llash
	$(VENV)/alembic upgrade head

revision:  ## Yangi migratsiya yaratish: make revision m="izoh"
	$(VENV)/alembic revision --autogenerate -m "$(m)"

test:  ## Testlarni ishga tushirish
	$(VENV)/pytest

cov:  ## Testlar + qamrov hisoboti
	$(VENV)/pytest --cov=app --cov-report=term-missing

lint:  ## Kodni tekshirish
	$(VENV)/ruff check app tests scripts

fmt:  ## Kodni formatlash
	$(VENV)/ruff check --fix app tests scripts
	$(VENV)/ruff format app tests scripts

client:  ## Yangi API mijoz yaratish: make client name="Bot"
	$(VENV)/python -m scripts.create_client --name "$(name)"

docker-up:  ## Docker orqali ko'tarish
	docker compose up -d --build

docker-down:  ## Docker konteynerlarni to'xtatish
	docker compose down
