.PHONY: install test lint api demo eval docker-build docker-up docker-down deploy deploy-status

install:
	python -m pip install -e '.[dev]'

test:
	python -m pytest

lint:
	python -m ruff check apps/api tests

api:
	uvicorn apps.api.src.main:app --reload --port 8000

demo:
	python3 -m http.server 4173 --directory apps/web-demo

eval:
	python -m apps.api.src.evaluation.cli --repeat 3

docker-build:
	docker build -t nba-agent:fixture .

docker-up:
	docker compose up --build

docker-down:
	docker compose down

deploy:
	docker compose up -d --build

deploy-status:
	docker compose ps
