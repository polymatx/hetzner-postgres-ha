# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-18

First release: one command for a PostgreSQL high-availability cluster on Hetzner Cloud.

### Added

- Terraform for Hetzner Cloud: private network, three database nodes in a spread placement
  group, a Volume per node for `PGDATA`, a cloud firewall that allows only SSH from your IP,
  and a private Load Balancer with services on 5432 (PostgreSQL), 6432 (PgBouncer) and 5433
  (replicas) that health-check Patroni's `/primary` and `/replica`
- Ansible roles: `common` (PGDG repository, private network matched by MAC, automatic upgrades
  off), `hardening` (sshd, ufw, sysctl), `etcd` 3.6, `patroni` (PostgreSQL 18, ordered
  bootstrap, Volume guard, softdog watchdog, dynamic configuration applied through the REST
  API), `pgbouncer` (SCRAM user list, restart on demotion) and `pgbackrest` (encrypted S3
  repository on Hetzner Object Storage, first full backup, daily timer on the primary)
- `make up`, `provision`, `status`, `psql`, `failover-test`, `backup`, `restore-test`, `down`
- `make test`: three systemd containers, HAProxy and SeaweedFS run the whole stack locally;
  CI runs it on every push together with terraform validate, tflint, ansible-lint, yamllint
  and shellcheck
- Release workflow: pushing a `v*` tag publishes its GitHub Release with notes from this file

### Verified

- On Hetzner (three CPX22 in hel1): a new leader 24 s after the outage began, writes back
  after 27 s, no acknowledged write lost, the old leader rejoining as a replica after a reboot,
  and a second provisioning run changing nothing

[0.1.0]: https://github.com/polymatx/hetzner-postgres-ha/releases/tag/v0.1.0
