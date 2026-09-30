SANDBOX_IMAGE ?= issue-to-pr-sandbox:dev

.PHONY: sandbox-image test test-docker validate

sandbox-image:
	docker build -t $(SANDBOX_IMAGE) sandbox_image

test:
	uv run pytest tests/unit -q

test-docker: sandbox-image
	uv run pytest -m docker -q

validate:
	uv run python -m bench.validate
