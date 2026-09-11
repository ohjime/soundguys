ifeq ($(OS),Windows_NT)
CLEAR := cls
REMOTE_ENV := set COSOUND_API_URL=https://api.cosound.ca &&
else
CLEAR := reset
REMOTE_ENV := COSOUND_API_URL=https://api.cosound.ca
endif

server:
ifeq ($(run),clean)
	@echo "Cleaning Server..."
	@echo "Deleting Python Cache..."
	@cd src/server/src \
		&& find . -type d -name "__pycache__" -exec rm -rf {} +
	@echo "Deleting Django Migrations..."
	@cd src/server/src \
		&& find . -type f -path "*/migrations/*.py" ! -name "__init__.py" ! -path "*/core/migrations/0001_initial.py" -exec rm -f {} +
	@echo "Deleting Databases..."
	@-dropdb --force cosounds > /dev/null 2>&1
	@echo "Deleting Node Modules..."
	@cd src/server/vite \
		&& find . -type d -name "node_modules" -exec rm -rf {} +
	@echo "Deleting Vite Builds..."
	@cd src/server/vite \
		&& find . -type d -name "static" -exec rm -rf {} +	
	@echo "Deleting Virtual Environments..."
	@find . -type d -name ".venv" -exec rm -rf {} +
else ifdef run
	@echo "Running command in Server Environment..."
	@cd src/server \
		&& $(run)
else
	@reset
	@echo "Player WebSockets require Redis: run 'make redis' first, or configure REDIS_URL."
	@chmod +x bin/clean_honcho.sh
	@echo "Cleaning Orphaned Django Processes..."
	@./bin/clean_honcho.sh
	@echo "Installing dependencies..."
	@cd src/server \
		&& uv sync
	@echo "Setting Up Database..."
	@-createdb cosounds
	@echo "Building Vite Assets..."
	@cd src/server/vite \
		&& npm install \
		&& npm run build
	@echo "Making Migrations..."
	@cd src/server \
		&& uv run src/main.py makemigrations \
		&& uv run src/main.py migrate
	@echo "Creating cache table..."
	@cd src/server \
		&& uv run src/main.py createcachetable
	@echo "Running tests..."
	@echo "All tests passed!"
	@echo "Server is ready!"
	@echo "Starting Server..."
	@cd src/server \
		&& uv run src/main.py proc runserver --procfile procfile.dev
endif

# Standalone Redis for a native development server; requires Docker Compose.
DC_REDIS = docker compose -f docker/docker-compose.redis.yml --project-directory . --project-name cosound-redis-dev

.PHONY: redis redis-down redis-logs

redis:
	$(DC_REDIS) up -d --wait

redis-down:
	$(DC_REDIS) down

redis-logs:
	$(DC_REDIS) logs -f redis

superuser:
	@echo "Creating superuser for Central Backend..."
	@cd src/server \
		&& uv sync
	@cd src/server \
		&& uv run src/main.py createsuperuser

player:
ifeq ($(run),clean)
	@echo "Cleaning Player Environment..."
	@echo "Deleting cosound.json..."
	@rm -f src/player/cosound.json
	@echo "Deleting tmp folder..."
	@rm -rf src/player/tmp
	@echo "Deleting Virtual Environments..."
	@rm -rf src/player/.venv
	@echo "Deleting Python Cache..."
	@cd src/player/src \
		&& find . -type d -name "__pycache__" -exec rm -rf {} +
else ifeq ($(run),remote)
	@$(CLEAR)
	@echo "Starting Player against remote API (https://api.cosound.ca)..."
	@cd src/player \
		&& uv sync
	@cd src/player \
		&& $(REMOTE_ENV) uv run src/main.py $(if $(token),--token=$(token)) $(if $(master_gain),--master-gain=$(master_gain))
else ifdef run
	@echo "Running command in Player Environment..."
	@cd src/player \
		&& $(run)
else
	@$(CLEAR)
	@cd src/player \
		&& uv sync
	@cd src/player \
		&& uv run src/main.py $(if $(token),--token=$(token)) $(if $(master_gain),--master-gain=$(master_gain))
endif

# --- Docker / production ---
DC = docker compose -f docker/docker-compose.yml --project-directory . --env-file env/.env

.PHONY: docker-env docker-build docker-up docker-down docker-restart docker-logs docker-logs-web docker-ps docker-shell docker-migrate docker-prune docker-deploy

docker-env:
	@chmod +x bin/setup_env.sh
	@bin/setup_env.sh

docker-build:
	$(DC) build web

docker-up:
	$(DC) up -d

docker-down:
	$(DC) down

docker-restart:
	$(DC) restart

docker-logs:
	$(DC) logs -f

# Follow the Django app server (gunicorn/uvicorn): print() output, tracebacks,
# and request/access logs. Override line count with TAIL=N (default 100).
docker-logs-web:
	$(DC) logs -f --tail=$(or $(TAIL),100) web

docker-ps:
	$(DC) ps

docker-shell:
	$(DC) exec web python src/main.py shell

docker-migrate:
	$(DC) exec web python src/main.py migrate

docker-prune:
	docker image prune -f

docker-deploy:
	git pull --ff-only
	$(DC) build web
	$(DC) up -d
	docker image prune -f
