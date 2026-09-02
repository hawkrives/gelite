.PHONY: all

ROOT = $(dir $(realpath $(firstword $(MAKEFILE_LIST))))

all: \
	workflows/build.release.yml \
	workflows/build.testing.yml \
	workflows/build.dryrun.yml \
	workflows/tests.yml \
	workflows/tests.pool.yml \

workflows/build.%.yml: workflows.src/build.%.tpl.yml workflows.src/build.%.targets.yml workflows.src/build.inc.yml
	$(ROOT)/workflows.src/render.py --workflow=build build.$* build.$*.targets.yml

workflows/tests.yml: workflows.src/tests.tpl.yml workflows.src/tests.targets.yml workflows.src/tests.inc.yml
	$(ROOT)/workflows.src/render.py --workflow=test tests tests.targets.yml

workflows/tests.%.yml: workflows.src/tests.%.tpl.yml workflows.src/tests.%.targets.yml workflows.src/tests.inc.yml
	$(ROOT)/workflows.src/render.py --workflow=test tests.$* tests.$*.targets.yml
