SANDBOX_IMAGE ?= issue-to-pr-sandbox:dev

.PHONY: sandbox-image test test-docker validate eval

sandbox-image:
	docker build -t $(SANDBOX_IMAGE) sandbox_image

test:
	uv run pytest tests/unit -q

test-docker: sandbox-image
	uv run pytest -m docker -q

validate:
	uv run python -m bench.validate

# Quality evals (spends model credits, starts Docker sandboxes). The launcher raises
# agents-cli's 120 s read timeout; see AGENTS.md. A reused local server keeps its old
# configuration, hence --stop-server first. Compare attempted and graded cases after.
eval:
	agents-cli run --stop-server
	uv run python scripts/agents_cli_eval.py eval run --dataset tests/eval/datasets/pipeline-dev.json --config tests/eval/eval_config.yaml --concurrency 2
	@echo "leftover itp- containers (none expected):"
	@docker ps -a --filter name=itp- --format '{{.Names}} {{.Status}}'
