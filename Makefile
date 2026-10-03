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

.PHONY: install uninstall test release
