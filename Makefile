SANDBOX_IMAGE ?= issue-to-pr-sandbox:dev

# Cloud targets take the project from GOOGLE_CLOUD_PROJECT (export it in the shell; make
# does not read .env) and never write it down. Nothing cloud-facing is the default target.
PROJECT ?= $(GOOGLE_CLOUD_PROJECT)
WITH_PROJECT = GOOGLE_CLOUD_PROJECT=$(PROJECT)
SI = $(WITH_PROJECT) uv run python scripts/sandbox_infra.py
TF_SP_DIR = deployment/terraform/single-project
TF_BUDGET_DIR = deployment/terraform/budget
TF_SP = terraform -chdir=$(TF_SP_DIR)
TF_BUDGET = terraform -chdir=$(TF_BUDGET_DIR)
# Only set after the hard stop has unlinked billing (the root can no longer read it).
BILLING_VAR = $(if $(BILLING_ACCOUNT),-var billing_account=$(BILLING_ACCOUNT))
BUDGET_VARS = -var project_id=$(PROJECT) -var budget_amount_try=$(BUDGET_TRY) $(BILLING_VAR)
# budget-plan saves the plan the owner reads; budget-apply applies exactly that file.
BUDGET_PLAN = budget.tfplan
# Teardown names the engine from Terraform on every step, never from SANDBOX_ENGINE;
# an empty output makes sandbox_infra.py exit 2.
TF_ENGINE = "$$($(TF_SP) output -raw agent_runtime_resource_name)"
# Teardown waits for deletes to finish: WAIT_TRIES polls, WAIT_SECONDS apart.
WAIT_TRIES ?= 30
WAIT_SECONDS ?= 10

# State backups, outside the checkout (the files hold the project id: never commit or
# paste them). Exported, so a recipe says $TF_BACKUP_DIR and never prints the path.
TF_BACKUP_DIR ?= $(HOME)/.local/state/issue-to-pr/tfstate
export TF_BACKUP_DIR
# Copies each root's state files to $TF_BACKUP_DIR/<UTC stamp>/<root>/ (directories
# 700, files 600) and prints only how many it copied.
TF_BACKUP = umask 077; stamp=$$(date -u +%Y%m%dT%H%M%SZ); dest="$$TF_BACKUP_DIR/$$stamp"; \
	[ ! -e "$$dest" ] || dest="$$dest-$$$$"; n=0; \
	for root in $(TF_SP_DIR) $(TF_BUDGET_DIR); do for f in terraform.tfstate terraform.tfstate.backup; do \
	[ -f "$$root/$$f" ] || continue; d="$$dest/$${root\#\#*/}"; \
	mkdir -p "$$d" && chmod 700 "$$dest" "$$d" && cp "$$root/$$f" "$$d/$$f" && chmod 600 "$$d/$$f" || exit 1; \
	n=$$((n+1)); done; done; echo "tf-backup: copied $$n state files"
# After an apply or destroy, even a failed one: back up, keep the command's status.
TF_BACKUP_AFTER = s=$$?; ($(TF_BACKUP)) || s=1; exit $$s

# The project pin. $(1) is a Terraform root. A root whose state lists resources must
# have been applied with this project: its project_id output must equal
# GOOGLE_CLOUD_PROJECT. Neither value is printed. A root with no resources passes.
SAME_PROJECT = root="$(1)"; \
	if [ -s "$$root/terraform.tfstate" ]; then \
	resources=$$(terraform -chdir="$$root" state list 2>/dev/null) || { echo "error: cannot read the Terraform state in $$root (run terraform -chdir=$$root init -input=false, then retry); refusing" >&2; exit 2; }; \
	else resources=$$(terraform -chdir="$$root" state list 2>/dev/null) || resources=""; fi; \
	[ -n "$$resources" ] || exit 0; \
	pinned=$$(terraform -chdir="$$root" output -raw project_id 2>/dev/null) || pinned=""; \
	[ -n "$$pinned" ] || { echo "error: the Terraform state in $$root has no project_id output (it predates the project pin); make refuses: run terraform plan there by hand and check by eye that it is for this project" >&2; exit 2; }; \
	[ "$$pinned" = "$(PROJECT)" ] || { echo "error: the Terraform state in $$root belongs to another project than GOOGLE_CLOUD_PROJECT; refusing" >&2; exit 2; }

.DEFAULT_GOAL := help

.PHONY: help sandbox-image test test-docker validate eval tf-fmt-check tf-backup \
	require-project require-budget-try require-confirm require-budget-plan \
	require-same-project-infra require-same-project-budget \
	infra-plan infra-apply budget-plan budget-apply budget-guard-test \
	sandbox-cloud test-cloud sweep-sandboxes deploy-stage deploy-check deploy \
	smoke-deployed teardown-dry-run teardown teardown-budget

help:
	@echo "Local and free: sandbox-image test test-docker validate tf-fmt-check tf-backup deploy-stage deploy-check"
	@echo "Read-only cloud (need GOOGLE_CLOUD_PROJECT): infra-plan budget-plan (also BUDGET_TRY) teardown-dry-run sweep-sandboxes"
	@echo "Create, change, destroy or spend (owner approval, AGENTS.md hard rule 8):"
	@echo "  infra-apply budget-apply budget-guard-test sandbox-cloud test-cloud deploy smoke-deployed"
	@echo "  teardown teardown-budget (also CONFIRM=yes)"
	@echo "Spends credits: eval"

# Guards. Every cloud target lists the ones it needs first.
require-project:
	@test -n "$(PROJECT)" || { echo "error: GOOGLE_CLOUD_PROJECT is not set" >&2; exit 2; }

require-budget-try:
	@case "$(BUDGET_TRY)" in ''|*[!0-9]*|0) echo "error: BUDGET_TRY must be a whole number of lira, at least 1 (\$$500 at the day's rate)" >&2; exit 2;; esac

require-confirm:
	@test "$(CONFIRM)" = "yes" || { echo "error: this deletes cloud resources; rerun with CONFIRM=yes" >&2; exit 2; }

require-budget-plan:
	@test -f "$(TF_BUDGET_DIR)/$(BUDGET_PLAN)" || { echo "error: no saved budget plan; run make budget-plan first and read it" >&2; exit 2; }

require-same-project-infra: require-project
	@$(call SAME_PROJECT,$(TF_SP_DIR))

require-same-project-budget: require-project
	@$(call SAME_PROJECT,$(TF_BUDGET_DIR))

# Copies both roots' state files out of the checkout; also runs before and after every
# apply and destroy.
tf-backup:
	@$(TF_BACKUP)

# Poll a dry-run listing until it shows nothing left to delete (deletes return before they
# finish). $(1) is the listing command, $(2) what is being waited for.
define wait-clear
@n=0; while :; do out=$$($(1)) || exit 1; echo "$$out" | grep -q '^would delete' || break; n=$$((n+1)); [ $$n -le $(WAIT_TRIES) ] || { echo "error: $(2) still there after $(WAIT_TRIES) polls" >&2; exit 1; }; echo "waiting for $(2) to finish deleting ($$n/$(WAIT_TRIES))"; sleep $(WAIT_SECONDS); done
endef

sandbox-image:
	docker build -t $(SANDBOX_IMAGE) sandbox_image

test:
	uv run pytest tests/unit -q

test-docker: sandbox-image
	uv run pytest -m docker -q

validate:
	uv run python -m bench.validate

# terraform fmt runs per root; the cicd root is not touched.
tf-fmt-check:
	$(TF_SP) fmt -check
	$(TF_BUDGET) fmt -check

# Quality evals (spends model credits, starts Docker sandboxes). The launcher raises
# agents-cli's 120 s read timeout; see AGENTS.md. A reused local server keeps its old
# configuration, hence --stop-server first. Compare attempted and graded cases after.
eval:
	-agents-cli run --stop-server  # exits 1 when no server runs; that is fine
	uv run python scripts/agents_cli_eval.py eval run --dataset tests/eval/datasets/pipeline-dev.json --config tests/eval/eval_config.yaml --concurrency 2
	@echo "leftover itp- containers (none expected):"
	@docker ps -a --filter name=itp- --format '{{.Names}} {{.Status}}'

# ---- Terraform: single-project root (plan is read-only; apply needs owner approval) ----

infra-plan: require-project require-same-project-infra
	@echo "will: plan the single-project root (read-only)"
	agents-cli infra single-project --project $(PROJECT)

# agents-cli always applies with -auto-approve: the project pin is the guard here.
infra-apply: require-project require-same-project-infra
	@echo "will: CREATE OR CHANGE cloud resources in the single-project root (owner approval)"
	@$(TF_BACKUP)
	agents-cli infra single-project --project $(PROJECT) --apply; $(TF_BACKUP_AFTER)

# ---- Terraform: budget root and the hard stop ----

budget-plan: require-project require-budget-try require-same-project-budget
	@echo "will: init and plan the budget root (read-only); budget $(BUDGET_TRY) TRY; saves the plan for budget-apply"
	@rm -f $(TF_BUDGET_DIR)/$(BUDGET_PLAN)
	$(TF_BUDGET) init -input=false
	$(TF_BUDGET) plan -input=false -out=$(BUDGET_PLAN) $(BUDGET_VARS)

# Applies exactly the plan budget-plan saved (Terraform refuses it once stale).
budget-apply: require-project require-same-project-budget require-budget-plan
	@echo "will: CREATE OR CHANGE the budget, its topic and the billing-disabling function as in the saved plan (owner approval)"
	@$(TF_BACKUP)
	$(TF_BUDGET) apply -input=false $(BUDGET_PLAN); $(TF_BACKUP_AFTER)
	@rm -f $(TF_BUDGET_DIR)/$(BUDGET_PLAN)

budget-guard-test: require-project require-same-project-budget
	@echo "will: publish one dry-run message to the budget topic and read the function's log (owner approval; never disables billing)"
	$(WITH_PROJECT) uv run python scripts/budget_guard_check.py

# ---- Sandboxes ----

sandbox-cloud: require-project require-same-project-infra
	@echo "will: build the sandbox image if it is new and create the sandbox template if there is none (owner approval)"
	$(SI) image
	$(SI) template

test-cloud: require-project require-same-project-infra
	@echo "will: start real Agent Runtime sandboxes (spends credits; owner approval)"
	ITP_CLOUD_TESTS=1 $(WITH_PROJECT) uv run --env-file .env pytest -m "cloud or cloud_slow" -q -rP

sweep-sandboxes: require-project require-same-project-infra
	@echo "will: delete sandboxes older than SANDBOX_TTL_S under SANDBOX_ENGINE"
	$(SI) sweep

# ---- Deploy (only build/deploy/ is uploaded; never run agents-cli deploy from the project root) ----

deploy-stage:
	uv run python scripts/stage_deploy.py stage

deploy-check: deploy-stage
	docker build -t issue-to-pr-agent:check build/deploy

deploy: require-project require-same-project-infra
	@echo "will: stage build/deploy and DEPLOY the bench graph to Agent Runtime (owner approval)"
	$(WITH_PROJECT) uv run python scripts/stage_deploy.py deploy

smoke-deployed: require-project require-same-project-infra
	@echo "will: run one task on the deployed agent (calls a model; owner approval)"
	$(WITH_PROJECT) uv run python scripts/stage_deploy.py smoke

# ---- Teardown ----

teardown-dry-run: require-project require-same-project-infra
	@echo "will: list what teardown would remove; nothing changes"
	$(SI) sweep --all --dry-run --engine $(TF_ENGINE)
	$(SI) prune-templates --all --dry-run --engine $(TF_ENGINE)
	$(SI) delete-engine --name $(TF_ENGINE) --expect-display-name issue-to-pr --dry-run
	$(TF_SP) plan -destroy -input=false -var project_id=$(PROJECT)
	gcloud storage ls --project=$(PROJECT) gs://$(PROJECT)_cloudbuild

teardown: require-project require-confirm require-same-project-infra
	@echo "will: DELETE all sandboxes, all templates, the engine, the single-project root and the cloudbuild bucket (owner approval)"
	@echo "step 1/5: sandboxes"
	$(SI) sweep --all --engine $(TF_ENGINE)
	$(call wait-clear,$(SI) sweep --all --dry-run --engine $(TF_ENGINE),sandboxes)
	@echo "step 2/5: templates"
	$(SI) prune-templates --all --engine $(TF_ENGINE)
	$(call wait-clear,$(SI) prune-templates --all --dry-run --engine $(TF_ENGINE),templates)
	@echo "step 3/5: engine (waits until it is gone)"
	$(SI) delete-engine --name $(TF_ENGINE) --expect-display-name issue-to-pr
	@echo "step 4/5: single-project Terraform root"
	@$(TF_BACKUP)
	$(TF_SP) destroy -auto-approve -var project_id=$(PROJECT); $(TF_BACKUP_AFTER)
	@echo "step 5/5: cloudbuild bucket"
	@if gcloud storage ls --project=$(PROJECT) gs://$(PROJECT)_cloudbuild >/dev/null 2>&1; then gcloud storage rm -r gs://$(PROJECT)_cloudbuild; else echo "no cloudbuild bucket"; fi
	@echo "budget root kept: make teardown-budget removes it"

# Removes the hard stop; always last, and only when the owner asks. The variable
# budget_amount_try must pass its validation (whole lira >= 1) even on destroy, so 1.
# After the hard stop has fired, pass BILLING_ACCOUNT=<id> (billing is unlinked).
teardown-budget: require-project require-confirm require-same-project-budget
	@echo "will: DESTROY the budget, its topic and the guard function, then offer to delete gcf-artifacts and gcf-v2-* (owner approval)"
	$(TF_BUDGET) init -input=false
	@$(TF_BACKUP)
	$(TF_BUDGET) destroy -auto-approve -var project_id=$(PROJECT) -var budget_amount_try=1 $(BILLING_VAR); $(TF_BACKUP_AFTER)
	@echo "left behind by the function build:"
	@gcloud artifacts repositories list --project=$(PROJECT) --location=us-central1 --filter="name~/gcf-artifacts$$" --format="value(name)"
	@gcloud storage buckets list --project=$(PROJECT) --filter="name~^gcf-v2-" --format="value(name)"
	@printf "delete the repository and buckets listed above? [y/N] "; read a; if [ "$$a" = y ]; then gcloud artifacts repositories delete gcf-artifacts --project=$(PROJECT) --location=us-central1 --quiet; for b in $$(gcloud storage buckets list --project=$(PROJECT) --filter="name~^gcf-v2-" --format="value(name)"); do gcloud storage rm -r gs://$$b; done; else echo "left in place"; fi
