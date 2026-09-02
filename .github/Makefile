.PHONY: all

ROOT = $(dir $(realpath $(firstword $(MAKEFILE_LIST))))

all: \
	workflows/tests.yml


workflows/tests.yml: workflows.src/tests.tpl.yml workflows.src/tests.targets.yml workflows.src/tests.inc.yml
	$(ROOT)/workflows.src/render.py --workflow=test tests tests.targets.yml
