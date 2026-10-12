.DEFAULT_GOAL := help

UV ?= uv
FLY ?= $(if $(wildcard $(HOME)/.fly/bin/fly),$(HOME)/.fly/bin/fly,fly)
PORT ?= 8000
export SITE PORT

.PHONY: help crawl serve reset test deploy

help:
	@echo 'make crawl SITE=nadia.xyz  Crawl or resume one listed site'
	@echo 'make serve                Start local search, crawl controls, and stats'
	@echo 'make reset                Clear local crawl data (asks for confirmation)'
	@echo 'make test                 Run offline tests'
	@echo 'make deploy               Publish current local search results to Fly.io'
	@echo 'Optional: make serve PORT=8001'

crawl:
	@test -n "$$SITE" || { echo 'Usage: make crawl SITE=nadia.xyz'; exit 1; }
	$(UV) run python -m hypertext_search crawl "$$SITE"

serve:
	$(UV) run python -m hypertext_search serve --port "$$PORT"

reset:
	$(UV) run python -m hypertext_search reset

test:
	$(UV) run pytest -q

deploy:
	$(UV) run python -m scripts.snapshot
	$(FLY) deploy --remote-only --ha=false
