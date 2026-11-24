LUA ?= lua

.PHONY: icon lint test-server test-lua test package

icon:
	python3 tools/app_icon.py

lint:
	python3 tools/lint.py

test-server:
	python3 -m unittest discover -s server/tests -v

test-lua:
	$(LUA) tools/tests/test_app.lua

test: lint test-server test-lua

package: icon test
	python3 tools/package_local.py
