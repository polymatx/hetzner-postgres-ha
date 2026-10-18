# Contributing

Thanks for helping make PostgreSQL HA on Hetzner boring.

## Ground rules

- `make lint` and `make test` pass. `make test` runs the whole cluster in Docker, including a
  failover, a backup and a point-in-time restore, so a change to a role is tested without a
  Hetzner account.
- A second Ansible run changes nothing. `make test` checks it.
- New settings get a default that is safe for production and a line in the README.
- Changes to the design (failover timing, replication mode, routing, fencing) explain the
  trade-off in the pull request. The reasoning behind the current design is in the
  [blog post](https://polymatx.dev/blog/posts/postgres-ha-patroni-hetzner/).

## Getting started

You need Docker, Terraform 1.9+, Python 3.12+ and `make`.

```bash
git clone https://github.com/polymatx/hetzner-postgres-ha.git
cd hetzner-postgres-ha
make deps
make lint
make test
make test-down
```

`make test-logs` prints the Patroni, etcd and PgBouncer journals when something fails.

## Testing on Hetzner

`make up` creates real servers that cost money (about €0.12 an hour for the default cluster).
Run `make failover-test`, `make backup` and `make restore-test`, then `make down`. Delete the
backup bucket in the console afterwards if you don't need it.

## Reporting bugs

Open an issue with the failing command or Ansible task, the release or commit, and your
location and server type. Security problems go to the address in [SECURITY.md](SECURITY.md).
