.PHONY: install test lint api demo eval docker-build docker-up configure-siliconflow-key docker-up-silicon docker-down deploy deploy-status

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

configure-siliconflow-key:
	./scripts/configure-siliconflow-key.sh

docker-up-silicon:
	@test -s secrets/siliconflow_api_key || { echo "缺少 secrets/siliconflow_api_key；先运行 make configure-siliconflow-key" >&2; exit 1; }
	docker compose -f docker-compose.yml -f docker-compose.siliconflow.yml up --build

docker-down:
	docker compose down

deploy:
	docker compose up -d --build

deploy-status:
	docker compose ps
