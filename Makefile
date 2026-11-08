PYTHON ?= python3
CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra

.PHONY: test check c-test clean

test:
	$(PYTHON) tests/run.py

check: test c-test
	$(PYTHON) -m compileall -q gui/app cellular tools
	@for file in gui/bin/* audio/jtb1-* wifi/jtb1-* display/jtb1-battery-led; do sh -n "$$file" || exit; done

build:
	mkdir -p build

build/fb-present: display/fb_present.c | build
	$(CC) $(CFLAGS) $< -o $@

build/touch-feedback: touch/touch.c | build
	$(CC) $(CFLAGS) $< -o $@

build/test-touch: touch/test_touch.c touch/touch.c | build
	$(CC) $(CFLAGS) touch/test_touch.c -o $@

build/nl-scan-compat.so: wifi/nl_scan_compat.c | build
	$(CC) $(CFLAGS) -shared -fPIC $< -ldl -o $@

build/setreg-jp.so: wifi/setreg_jp.c | build
	$(CC) $(CFLAGS) -shared -fPIC $< -o $@

c-test: build/fb-present build/touch-feedback build/test-touch build/nl-scan-compat.so build/setreg-jp.so
	./build/test-touch
	./build/touch-feedback --selftest

clean:
	rm -rf build
