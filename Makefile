BIN ?= $(HOME)/.local/bin

install:
	ln -sf $(CURDIR)/unplayer $(BIN)/unplayer

uninstall:
	rm -f $(BIN)/unplayer

test:
	./test/test.sh

.PHONY: install uninstall test
