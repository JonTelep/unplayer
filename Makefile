BIN ?= $(HOME)/.local/bin
VERSION := $(shell sed -n 's/^VERSION=//p' unplayer)

install:
	mkdir -p $(BIN)
	ln -sf $(CURDIR)/unplayer $(BIN)/unplayer

uninstall:
	rm -f $(BIN)/unplayer

test:
	./test/test.sh

# bump VERSION= in ./unplayer first, then: make release
release: test
	git tag -a v$(VERSION) -m "unplayer v$(VERSION)"
	git push origin main v$(VERSION)
	gh release create v$(VERSION) unplayer --title "unplayer v$(VERSION)" --generate-notes

# web player + Caddy (HTTPS) in Docker; needs .env (cp .env.example .env)
deploy:
	@test -f .env || { echo 'cp .env.example .env and set DOMAIN' >&2; exit 1; }
	docker compose up -d --build

# just the app on this machine, no Caddy/HTTPS: http://localhost:8080
local:
	@test -f .env || { echo 'cp .env.example .env' >&2; exit 1; }
	docker compose up -d --build app
	docker compose rm -sf caddy
	@echo 'unplayer is up: http://localhost:8080'

# stop and remove what deploy started (containers, network, built image); keeps stats + TLS cert volumes.
# Wipe those too: docker compose down -v
clean:
	docker compose down --rmi local

.PHONY: install uninstall test release deploy local clean
