.PHONY: install test lint fmt seed serve web dogfood docker
PYDIRS := packages/core packages/audio packages/embed packages/index packages/match packages/library services/api services/indexer cli
export PYTHONPATH := $(shell echo $(PYDIRS) | tr ' ' ':'):.

install:
	pip install -e ".[dev]"

test:
	pytest -q

lint:
	ruff check .

fmt:
	ruff check --fix .
	ruff format .

seed:
	python scripts/seed_fixtures.py ./data/audio

serve:
	uvicorn clawhum_api.app:app --host 0.0.0.0 --port 7451 --reload

web:
	cd web && npm install && npm run dev

dogfood: install seed
	clawhum index ./data/audio --no-clap
	clawhum stats
	$(MAKE) serve

docker:
	docker build -f infra/docker/Dockerfile -t clawhum/api:dev .
