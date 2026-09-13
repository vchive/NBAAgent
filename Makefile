.PHONY: install test lint api demo eval solution-pdf docker-build docker-build-nba docker-up docker-up-nba configure-siliconflow-key configure-aliyun-iqs-key configure-qianfan-search-key configure-app-password docker-up-auth docker-up-silicon docker-up-qianfan docker-down deploy deploy-live deploy-live-qianfan warm-2026-cache warm-game-index deploy-status

install:
	python3 -m pip install -e '.[dev]'

test:
	python3 -m pytest

lint:
	python3 -m ruff check apps/api tests

api:
	uvicorn apps.api.src.main:app --reload --host "$${BIND_HOST:-127.0.0.1}" --port "$${BIND_PORT:-8000}"

demo:
	python3 -m http.server 4173 --bind 127.0.0.1 --directory apps/web-demo

eval:
	python3 -m apps.api.src.evaluation.cli --repeat 3

solution-pdf:
	python3 scripts/build-solution-pdf.py

docker-build:
	docker build -t flower-agent:fixture .

# Explicit migration alias for scripts that still expect the former image tag.
docker-build-nba:
	docker build -t nba-agent:fixture .

docker-up:
	docker compose up --build

# The old service name is available only through the opt-in compatibility
# profile, so running this target cannot start duplicate flower containers.
docker-up-nba:
	docker compose --profile nba-compat up --build nba-agent

configure-siliconflow-key:
	./scripts/configure-siliconflow-key.sh

configure-aliyun-iqs-key:
	./scripts/configure-aliyun-iqs-key.sh

configure-qianfan-search-key:
	./scripts/configure-qianfan-search-key.sh

configure-app-password:
	./scripts/configure-app-password.sh

docker-up-auth:
	@test -s secrets/app_password || { echo "缺少 secrets/app_password；先运行 make configure-app-password" >&2; exit 1; }
	docker compose -f docker-compose.yml -f docker-compose.auth.yml up --build

docker-up-silicon:
	@test -s secrets/app_password || { echo "缺少 secrets/app_password；先运行 make configure-app-password" >&2; exit 1; }
	@test -s secrets/siliconflow_api_key || { echo "缺少 secrets/siliconflow_api_key；先运行 make configure-siliconflow-key" >&2; exit 1; }
	@test -s secrets/aliyun_iqs_api_key || { echo "缺少 secrets/aliyun_iqs_api_key；先运行 make configure-aliyun-iqs-key" >&2; exit 1; }
	@test -s secrets/qianfan_search_api_key || { echo "缺少 secrets/qianfan_search_api_key；先运行 make configure-qianfan-search-key" >&2; exit 1; }
	docker compose -f docker-compose.yml -f docker-compose.auth.yml -f docker-compose.siliconflow.yml up --build

docker-down:
	docker compose down

deploy:
	@test -s secrets/app_password || { echo "缺少 secrets/app_password；先运行 make configure-app-password" >&2; exit 1; }
	docker compose -f docker-compose.yml -f docker-compose.public.yml -f docker-compose.auth.yml up -d --build --force-recreate

deploy-live:
	@test -s secrets/app_password || { echo "缺少 secrets/app_password；先运行 make configure-app-password" >&2; exit 1; }
	@test -s secrets/siliconflow_api_key || { echo "缺少 secrets/siliconflow_api_key；先运行 make configure-siliconflow-key" >&2; exit 1; }
	@test -s secrets/aliyun_iqs_api_key || { echo "缺少 secrets/aliyun_iqs_api_key；先运行 make configure-aliyun-iqs-key" >&2; exit 1; }
	@test -s secrets/qianfan_search_api_key || { echo "缺少 secrets/qianfan_search_api_key；先运行 make configure-qianfan-search-key" >&2; exit 1; }
	docker compose -f docker-compose.yml -f docker-compose.public.yml -f docker-compose.auth.yml -f docker-compose.siliconflow.yml up -d --build --force-recreate

docker-up-qianfan:
	@test -s secrets/app_password || { echo "缺少 secrets/app_password；先运行 make configure-app-password" >&2; exit 1; }
	@test -s secrets/siliconflow_api_key || { echo "缺少 secrets/siliconflow_api_key；先运行 make configure-siliconflow-key" >&2; exit 1; }
	@test -s secrets/aliyun_iqs_api_key || { echo "缺少 secrets/aliyun_iqs_api_key；先运行 make configure-aliyun-iqs-key" >&2; exit 1; }
	@test -s secrets/qianfan_search_api_key || { echo "缺少 secrets/qianfan_search_api_key；先运行 make configure-qianfan-search-key" >&2; exit 1; }
	docker compose -f docker-compose.yml -f docker-compose.auth.yml -f docker-compose.siliconflow.yml -f docker-compose.qianfan.yml up --build

deploy-live-qianfan:
	@test -s secrets/app_password || { echo "缺少 secrets/app_password；先运行 make configure-app-password" >&2; exit 1; }
	@test -s secrets/siliconflow_api_key || { echo "缺少 secrets/siliconflow_api_key；先运行 make configure-siliconflow-key" >&2; exit 1; }
	@test -s secrets/aliyun_iqs_api_key || { echo "缺少 secrets/aliyun_iqs_api_key；先运行 make configure-aliyun-iqs-key" >&2; exit 1; }
	@test -s secrets/qianfan_search_api_key || { echo "缺少 secrets/qianfan_search_api_key；先运行 make configure-qianfan-search-key" >&2; exit 1; }
	docker compose -f docker-compose.yml -f docker-compose.public.yml -f docker-compose.auth.yml -f docker-compose.siliconflow.yml -f docker-compose.qianfan.yml up -d --build --force-recreate

warm-2026-cache:
	@test -s secrets/app_password || { echo "缺少 secrets/app_password；先运行 make configure-app-password" >&2; exit 1; }
	@test -s secrets/siliconflow_api_key || { echo "缺少 secrets/siliconflow_api_key；先运行 make configure-siliconflow-key" >&2; exit 1; }
	@test -s secrets/aliyun_iqs_api_key || { echo "缺少 secrets/aliyun_iqs_api_key；先运行 make configure-aliyun-iqs-key" >&2; exit 1; }
	@test -s secrets/qianfan_search_api_key || { echo "缺少 secrets/qianfan_search_api_key；先运行 make configure-qianfan-search-key" >&2; exit 1; }
	docker compose -f docker-compose.yml -f docker-compose.public.yml -f docker-compose.auth.yml -f docker-compose.siliconflow.yml exec -T flower-agent python /app/scripts/warm-2026-cache.py --details

warm-game-index:
	@test -s secrets/app_password || { echo "缺少 secrets/app_password；先运行 make configure-app-password" >&2; exit 1; }
	docker compose -f docker-compose.yml -f docker-compose.public.yml -f docker-compose.auth.yml -f docker-compose.siliconflow.yml exec -T flower-agent python /app/scripts/warm-game-index.py --season 2025-26 --teams all --from 2025-09-01 --to 2026-07-01 --details

deploy-status:
	docker compose -f docker-compose.yml -f docker-compose.public.yml -f docker-compose.auth.yml ps
