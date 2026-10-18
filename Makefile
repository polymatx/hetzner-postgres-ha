SHELL := /bin/bash
.DEFAULT_GOAL := help

PYTHON ?= python3
TF ?= terraform
VENV := .venv
BIN := $(abspath $(VENV))/bin
INVENTORY ?= ansible/inventory/hosts.json
TEST_INVENTORY := tests/inventory.yml
COMPOSE := docker compose -f tests/docker-compose.yml
CLUSTER := $(PYTHON) scripts/cluster.py

export PATH := $(BIN):$(PATH)
export ANSIBLE_CONFIG := $(abspath ansible/ansible.cfg)

.PHONY: help deps up infra inventory provision down status psql failover-test backup restore-test \
	lint fmt validate tflint ansible-lint yamllint shellcheck \
	test test-up test-provision test-idempotence test-failover test-backup test-restore test-logs test-down

help:
	@echo "Hetzner Cloud"
	@echo "  make up              create the servers, network, firewall and load balancer, then configure the cluster"
	@echo "  make provision       re-run Ansible against the existing servers"
	@echo "  make status          members, endpoints and backups"
	@echo "  make psql            psql to the leader through the load balancer and PgBouncer"
	@echo "  make failover-test   power off the leader under writes and check the cluster recovers"
	@echo "  make backup          full pgBackRest backup now (TYPE=diff for a differential one)"
	@echo "  make restore-test    point-in-time restore into a scratch directory and verify it"
	@echo "  make down            destroy the servers, volumes and load balancer (backups stay in the bucket)"
	@echo ""
	@echo "Local, in Docker (no Hetzner account needed)"
	@echo "  make test            three nodes, provisioning, idempotence, failover, backup and restore"
	@echo "  make test-down       remove the test containers"
	@echo ""
	@echo "Development"
	@echo "  make deps            Python virtualenv with Ansible, plus the Ansible collections"
	@echo "  make lint            terraform fmt and validate, tflint, ansible-lint, yamllint, shellcheck"

deps: $(VENV)/.installed ansible/.collections/.installed

$(VENV)/.installed: requirements.txt
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --quiet --upgrade pip
	$(BIN)/pip install --quiet -r requirements.txt
	@touch $@

ansible/.collections/.installed: ansible/requirements.yml $(VENV)/.installed
	$(BIN)/ansible-galaxy collection install -r ansible/requirements.yml -p ansible/.collections
	@touch $@

up: deps infra inventory provision
	@$(CLUSTER) -i $(INVENTORY) status

infra:
	$(TF) -chdir=terraform init -input=false
	$(TF) -chdir=terraform apply -input=false $(if $(AUTO_APPROVE),-auto-approve,)

inventory:
	@umask 077 && $(TF) -chdir=terraform output -json ansible_inventory > $(INVENTORY)
	@echo "wrote $(INVENTORY)"

provision: deps
	$(BIN)/ansible-playbook -i $(INVENTORY) ansible/site.yml

down:
	$(TF) -chdir=terraform destroy -input=false $(if $(AUTO_APPROVE),-auto-approve,)
	rm -f $(INVENTORY) ansible/inventory/known_hosts

status:
	@$(CLUSTER) -i $(INVENTORY) status

psql:
	@$(CLUSTER) -i $(INVENTORY) psql

failover-test:
	@$(CLUSTER) -i $(INVENTORY) failover-test

backup:
	@$(CLUSTER) -i $(INVENTORY) backup --type $(or $(TYPE),full)

restore-test:
	@$(CLUSTER) -i $(INVENTORY) restore-test

lint: fmt validate tflint ansible-lint yamllint shellcheck

fmt:
	$(TF) fmt -check -recursive terraform

validate:
	$(TF) -chdir=terraform init -backend=false -input=false >/dev/null
	$(TF) -chdir=terraform validate

tflint:
	tflint --chdir=terraform --init
	tflint --chdir=terraform

ansible-lint: deps
	$(BIN)/ansible-lint

yamllint: deps
	$(BIN)/yamllint .

shellcheck:
	shellcheck ansible/roles/patroni/files/pg-role-callback ansible/roles/pgbouncer/files/pg-role-apply
	$(PYTHON) -c "import ast; ast.parse(open('scripts/cluster.py').read())"

test: test-up test-provision test-idempotence test-failover test-backup test-restore
	@echo "local test passed"

tests/.certs/public.crt:
	mkdir -p tests/.certs
	openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj /CN=s3 \
		-keyout tests/.certs/private.key -out tests/.certs/public.crt 2>/dev/null
	chmod 644 tests/.certs/private.key tests/.certs/public.crt

test-up: tests/.certs/public.crt
	$(COMPOSE) up -d --build --wait

test-provision: deps
	$(BIN)/ansible-playbook -i $(TEST_INVENTORY) ansible/site.yml

test-idempotence: deps
	@out=$$($(BIN)/ansible-playbook -i $(TEST_INVENTORY) ansible/site.yml) || { echo "$$out"; exit 1; }; \
	echo "$$out" | sed -n '/PLAY RECAP/,$$p'; \
	if echo "$$out" | grep -qE 'changed=[1-9]'; then echo "not idempotent:"; echo "$$out" | grep -B3 '^changed:'; exit 1; fi

test-failover:
	$(CLUSTER) -i $(TEST_INVENTORY) failover-test

test-backup:
	$(CLUSTER) -i $(TEST_INVENTORY) backup --type diff

test-restore:
	$(CLUSTER) -i $(TEST_INVENTORY) restore-test

test-logs:
	-for n in 1 2 3; do echo "=== pg-ha-$$n"; docker exec hpha-pg-ha-$$n journalctl --no-pager -n 80 -u patroni -u etcd -u pgbouncer; done
	-$(COMPOSE) logs --tail 50 lb s3

test-down:
	$(COMPOSE) down -v --remove-orphans
	rm -rf tests/.secrets tests/.certs
