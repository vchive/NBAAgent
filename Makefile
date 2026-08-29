.PHONY: install test lint api demo eval solution-pdf docker-build docker-up configure-siliconflow-key configure-app-password docker-up-auth docker-up-silicon docker-down deploy deploy-live deploy-status

install:
	python3 -m pip install -e '.[dev]'

test:
	python3 -m pytest

lint:
	python3 -m ruff check apps/api tests

api:
	uvicorn apps.api.src.main:app --reload --port 8000

demo:
	python3 -m http.server 4173 --directory apps/web-demo

eval:
	python3 -m apps.api.src.evaluation.cli --repeat 3

solution-pdf:
	python3 scripts/build-solution-pdf.py

docker-build:
	docker build -t nba-agent:fixture .

docker-up:
	docker compose up --build

configure-siliconflow-key:
	./scripts/configure-siliconflow-key.sh

configure-app-password:
	./scripts/configure-app-password.sh

docker-up-auth:
	@test -s secrets/app_password || { echo "缺少 secrets/app_password；先运行 make configure-app-password" >&2; exit 1; }
	docker compose -f docker-compose.yml -f docker-compose.auth.yml up --build

docker-up-silicon:
	@test -s secrets/app_password || { echo "缺少 secrets/app_password；先运行 make configure-app-password" >&2; exit 1; }
	@test -s secrets/siliconflow_api_key || { echo "缺少 secrets/siliconflow_api_key；先运行 make configure-siliconflow-key" >&2; exit 1; }
	docker compose -f docker-compose.yml -f docker-compose.auth.yml -f docker-compose.siliconflow.yml up --build

docker-down:
	docker compose down

deploy:
	@test -s secrets/app_password || { echo "缺少 secrets/app_password；先运行 make configure-app-password" >&2; exit 1; }
	docker compose -f docker-compose.yml -f docker-compose.public.yml -f docker-compose.auth.yml up -d --build --force-recreate

deploy-live:
	@test -s secrets/app_password || { echo "缺少 secrets/app_password；先运行 make configure-app-password" >&2; exit 1; }
	@test -s secrets/siliconflow_api_key || { echo "缺少 secrets/siliconflow_api_key；先运行 make configure-siliconflow-key" >&2; exit 1; }
	docker compose -f docker-compose.yml -f docker-compose.public.yml -f docker-compose.auth.yml -f docker-compose.siliconflow.yml up -d --build --force-recreate

deploy-status:
	docker compose -f docker-compose.yml -f docker-compose.public.yml -f docker-compose.auth.yml ps
