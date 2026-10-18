# hetzner-postgres-ha

[![CI](https://github.com/polymatx/hetzner-postgres-ha/actions/workflows/ci.yml/badge.svg)](https://github.com/polymatx/hetzner-postgres-ha/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![PostgreSQL 18](https://img.shields.io/badge/PostgreSQL-18-336791)
![Terraform](https://img.shields.io/badge/Terraform-%E2%89%A51.9-7B42BC)
![Ansible](https://img.shields.io/badge/Ansible-core%202.21-EE0000)

One command for a production-grade PostgreSQL high-availability cluster on Hetzner Cloud:
**Patroni + etcd + PgBouncer + pgBackRest behind a private Hetzner Load Balancer**, with automatic failover,
zero data loss for acknowledged commits, point-in-time recovery from Hetzner Object Storage, and a failover test
you can run against the real cluster.

```bash
make up
```

It automates the architecture from
[Postgres High Availability on Hetzner, Step by Step](https://polymatx.dev/blog/posts/postgres-ha-patroni-hetzner/),
the procedure behind a production billing database that has been through ten real leader changes.
The whole stack is tested in CI on every commit, in Docker, without spending money on Hetzner.

## What you get

| | |
|---|---|
| Failover | Automatic, about 35 s from a dead leader to a new one; the load balancer follows within one health check |
| Data loss | None for acknowledged commits while a synchronous standby is up (synchronous replication, `strict: off`) |
| Split brain | Leader lease in a 3-member etcd cluster, plus the kernel watchdog that resets a hung leader before its lease expires |
| Routing | One private address for the app; the load balancer sends traffic only to the node that answers `200` on Patroni's `/primary` |
| Pooling | PgBouncer on every node in transaction mode, capped at 80 server connections per database |
| Backups | pgBackRest: continuous WAL archiving plus weekly full and daily differential backups to Object Storage in another location, encrypted with AES-256 |
| Restore | Point-in-time recovery to any second in the retention window; `make restore-test` proves it |
| Storage | `PGDATA` on a Hetzner Volume that can be grown online; Patroni refuses to start if the Volume is missing |
| Security | Database ports reachable only from the private network, SSH only from your IP, SCRAM passwords, TLS on PostgreSQL |
| Operations | `make status`, `make psql`, `make failover-test`, `make backup`, `make restore-test`, `make down` |

## Architecture

```mermaid
flowchart TB
    app["Your app servers<br/>(attached to the same private network)"]
    lb["Hetzner Load Balancer, private only, 10.0.0.10<br/>6432 PgBouncer · 5432 PostgreSQL · 5433 replicas<br/>health check GET :8008/primary every 3 s"]
    app --> lb
    subgraph spread["Placement group 'spread': three different physical hosts"]
        n1["pg-ha-1 · 10.0.0.21<br/>PostgreSQL 18 leader<br/>Patroni · PgBouncer · etcd<br/>Volume for PGDATA"]
        n2["pg-ha-2 · 10.0.0.22<br/>synchronous standby<br/>Patroni · PgBouncer · etcd<br/>Volume for PGDATA"]
        n3["pg-ha-3 · 10.0.0.23<br/>asynchronous replica<br/>Patroni · PgBouncer · etcd<br/>Volume for PGDATA"]
    end
    lb ==>|"200: leader"| n1
    lb -.->|"503"| n2
    lb -.->|"503"| n3
    n1 -->|"synchronous WAL stream"| n2
    n1 -->|"WAL stream"| n3
    s3[("Hetzner Object Storage, other location<br/>pgBackRest WAL archive and backups, AES-256")]
    n1 -->|"archive-push"| s3
```

How it fails over, in four independent mechanisms:

1. **Leader election by lease.** The leader holds a key in etcd with a 30 s TTL and renews it every 10 s. If it stops renewing, the key expires and the synchronous standby takes it. etcd runs on all three nodes, so losing any one node keeps quorum, and a node that loses quorum demotes itself instead of risking two writers.
2. **Synchronous replication.** A commit is acknowledged only after the synchronous standby has it, so a failover loses nothing that was acknowledged. If the standby disappears, Patroni picks the other replica as the new synchronous standby, or runs standalone (`synchronous_mode_strict: off`) so writes continue.
3. **Routing by health check.** The load balancer health-checks Patroni's REST API. Only the leader answers `200` on `/primary`; the replicas answer `503`. Nothing calls a cloud API during failover. The Hetzner console shows the balancer as "mixed" health permanently; that is correct.
4. **Fencing.** Patroni arms the kernel watchdog while it holds the leader key. A leader whose Patroni process hangs is reset before its lease can expire under it.

One more piece closes the gap a TCP load balancer leaves: it decides where *new* connections go but never closes the existing ones. When a node is demoted, a Patroni callback writes its new role to a marker file and a root-owned systemd path unit restarts PgBouncer on that node, so applications reconnect and land on the new leader instead of getting `cannot execute UPDATE in a read-only transaction`.

## Quickstart

You need Terraform 1.9 or newer, Python 3.12 or newer (Ansible's requirement; on macOS use Homebrew's `python3`, not the system one), `make`, `ssh` and a Hetzner Cloud project.

1. In the [Hetzner Console](https://console.hetzner.cloud/), create an API token with read and write access in your project.
2. Optional but recommended: generate S3 credentials for Object Storage (your project, **Security**, **S3 credentials**). The bucket is created for you. Without credentials the cluster runs without backups.
3. Configure and start:

   ```bash
   git clone https://github.com/polymatx/hetzner-postgres-ha.git
   cd hetzner-postgres-ha
   cp terraform/terraform.tfvars.example terraform/terraform.tfvars
   export HCLOUD_TOKEN=your-api-token
   make up
   ```

   Edit `terraform/terraform.tfvars` before `make up`: put your SSH public key in `ssh_public_keys` and the S3 keys in `backup_s3_access_key` / `backup_s3_secret_key`. `make up` creates a Python virtualenv with Ansible, shows the Terraform plan and asks for confirmation, then configures the nodes. It takes about ten minutes.

4. Look at it and break it:

   ```bash
   make status
   make psql
   make failover-test
   make restore-test
   ```

5. Connect your application from a server in the same private network (attach it to the network named after `cluster_name`):

   ```
   DATABASE_URL=postgresql://appuser:PASSWORD@10.0.0.10:6432/appdb?sslmode=disable&connect_timeout=10
   DIRECT_URL=postgresql://appuser:PASSWORD@10.0.0.10:5432/appdb?sslmode=require&connect_timeout=10
   ```

   The password is in `ansible/inventory/.secrets/pg-ha/appuser`. Use the PgBouncer port 6432 for the application and port 5432 for migrations and admin tools, which cannot go through a transaction-mode pooler. Keep `connect_timeout`: without it a client can retry the dead address with exponential backoff and miss the switch by half a minute. Port 5433 spreads read-only connections over the replicas.

`make down` destroys the servers, the Volumes with the data on them, the network and the load balancer. The backup bucket is kept on purpose; delete it in the console when you no longer need the backups, otherwise the Object Storage base price keeps running.

## Cost

Monthly prices in euros, excluding VAT, for the default configuration in Germany or Finland, from Hetzner's price list after the April and June 2026 price changes (checked October 2026). Hetzner bills by the hour up to the monthly price.

| Item | Default | €/month |
|---|---|---|
| 3 database nodes | CPX22: 2 vCPU, 4 GB RAM, 80 GB NVMe, IPv4 included | 59.97 |
| Load balancer | LB11, private network only | 7.49 |
| 3 Volumes for PGDATA | 20 GB each at €0.0572/GB | 3.43 |
| Object Storage | base price, includes 1 TB storage and 1 TB egress | 6.49 |
| **Total** | | **77.38** |

Bigger nodes: three CPX32 (4 vCPU, 8 GB) bring the total to €125.38, three CPX42 (8 vCPU, 16 GB) to €227.38. The cheaper Cost-Optimized types (CX23 at €5.99, CAX11 at €6.49) would cut the total to about €35, but Hetzner marks them as unavailable at the time of writing; set `server_type = "cx23"` when they are back. Trying the cluster for an hour costs roughly €0.10 to €0.15.

## Failover demo

`make failover-test` picks a replica, writes a row every 0.2 s from it through the load balancer and PgBouncer, powers off the leader (Hetzner API `poweroff`, the same as pulling the plug), and then checks that a new leader takes over, that writes resume, that every acknowledged row is still there, and that the old leader rejoins as a replica when it is powered back on. This is a run of the same command in the Docker test environment:

```
Cluster before the test:
  member         role           state        TL       lag
  pg-ha-1        leader         running       1
  pg-ha-2        replica        streaming     1         0
  pg-ha-3        sync_standby   streaming     1         0

Writing a row every 0.2 s from pg-ha-2 through the load balancer (10.77.0.10:6432).
10 writes acknowledged. Powering off the leader pg-ha-1 now.

[   2.2s] first failed write: psql: error: connection to server at "10.77.0.10", port 6432 failed: timeout expired
[  35.5s] pg-ha-3 is the new leader
[  36.6s] writes are succeeding again

Powering pg-ha-1 back on and waiting for it to rejoin as a replica...

Cluster after the test:
  member         role           state        TL       lag
  pg-ha-1        replica        streaming     2         0
  pg-ha-2        sync_standby   streaming     2         0
  pg-ha-3        leader         running       2

Result
  killed leader          pg-ha-1 (timeline 1)
  new leader             pg-ha-3 after 35.5 s
  writes resumed after   36.6 s (longest gap between acknowledged writes 36.6 s)
  writes                 32 acknowledged, 112 failed
  acknowledged but lost  0
  old leader rejoined    after 4.2 s as replica
  pgbouncer reconciler   2026-10-18 00:12:45 role=replica -> restarted pgbouncer

PASS: the cluster failed over, writes continued and no acknowledged write was lost.
```

The time is dominated by the 30 s leader TTL. On real Hetzner servers the blog post measured the new leader at 33 s and applications writing again at about 40 s; the old leader takes longer to rejoin there than in Docker because the server has to boot. A planned switchover (`patronictl switchover`) takes about 10 s.

## Backups and restores

pgBackRest runs on every node but acts only where Patroni reports the primary, so backups follow the leader through failovers.

- **WAL archiving**: `archive_command` pushes every WAL segment to Object Storage as it is written (asynchronously, with a spool directory), and `archive_timeout` forces a segment at least every 5 minutes on a quiet database.
- **Base backups**: a systemd timer runs at 01:00 UTC: full on Sunday, differential on the other days. Retention keeps two full backups and 14 differentials. The first full backup is taken during `make up`, because point-in-time recovery starts at the first full backup.
- **Encryption**: the repository is encrypted client-side with `aes-256-cbc`. The passphrase is generated once and stored in `ansible/inventory/.secrets/<cluster>/pgbackrest-cipher`. **Keep a copy of that directory somewhere safe**: without the passphrase the backups cannot be restored.
- **Location**: the bucket lives in `backup_s3_region` (default `fsn1`) while the servers are in `location` (default `hel1`), so a site failure does not take the backups with it.

`make backup` takes a full backup now (`make backup TYPE=diff` for a differential). `make restore-test` proves that restores work: it writes one row on the leader, notes the time, writes a second row, waits until the WAL is archived, then restores the latest backup plus WAL to the noted time into a scratch data directory on a replica, starts it on port 5499, and checks that the first row exists and the second does not. The scratch instance runs with `archive_mode=off`, so it can never push WAL into the repository, and it is deleted afterwards.

To restore for real, run the same pgBackRest command on a scratch server with the same `pgbackrest.conf`, never on a cluster node's live data directory:

```bash
sudo -u postgres pgbackrest --stanza=pg-ha --type=time --target="2026-10-18 14:32:00+00" --target-action=promote restore
```

## Configuration

Everything about the infrastructure is set in `terraform/terraform.tfvars`:

| Variable | Default | Meaning |
|---|---|---|
| `cluster_name` | `pg-ha` | Patroni scope, pgBackRest stanza and prefix of every Hetzner resource |
| `location` | `hel1` | Location of the servers and the load balancer |
| `node_count` | `3` | 3 or 5 database nodes |
| `server_type` | `cpx22` | Hetzner server type |
| `volume_size` | `20` | GB per node for `PGDATA`; `0` keeps data on the local disk |
| `ssh_public_keys` / `ssh_key_names` | | Keys to upload, or names of keys already in the project |
| `admin_cidrs` | your current IPv4 | Who may reach SSH |
| `replica_endpoint` | `true` | Read-only service on port 5433 |
| `backup_s3_region` | `fsn1` | Object Storage location |
| `backup_s3_access_key` / `backup_s3_secret_key` | empty | S3 credentials; empty disables backups |
| `backup_bucket` | `<cluster>-pgbackrest-<random>` | Bucket name |

PostgreSQL and the services are tuned in Ansible. Shared settings (PostgreSQL version, database and user names) are in [`ansible/group_vars/all.yml`](ansible/group_vars/all.yml); everything else is in each role's `defaults/main.yml` and can be overridden in `ansible/group_vars/postgres.yml`. Memory settings are derived from the server's RAM (`shared_buffers` 25 %, `effective_cache_size` 75 %). Changes to PostgreSQL parameters are applied cluster-wide through Patroni's dynamic configuration on the next `make provision`; parameters that need a restart show up as pending in `patronictl list` and are applied with `patronictl restart pg-ha --pending`.

Passwords are generated on the first run and stored in `ansible/inventory/.secrets/<cluster>/`, which is ignored by git.

## Choices and changes from the blog post

The roles follow the blog post. These are the differences and why:

| Blog post | This repository | Why |
|---|---|---|
| Two PostgreSQL nodes plus an etcd-only witness | Three identical nodes, each with etcd | Any node can fail and there is still a synchronous standby; one server type is simpler to automate |
| PostgreSQL 16.15 | PostgreSQL 18.6 | Current major version; set `pg_version: 16` or `17` to stay on an older one |
| etcd 3.4.30 from Ubuntu | etcd 3.6.15 official binaries, checksum-verified | 3.4 is no longer maintained upstream |
| PgBouncer 1.25.2, pgBackRest 2.59.1 | PgBouncer 1.26.0, pgBackRest 2.59.3 | Current PGDG packages; Patroni stays at 4.1.5 |
| CX43 and CX23 servers | CPX22 | The CX and CAX lines cannot be ordered at the time of writing |
| `en_US.UTF-8` libc collation | Builtin `C.UTF-8` provider (PostgreSQL 17+) | Sort order no longer depends on the glibc version, so an OS upgrade or a replica on a newer image cannot silently corrupt text indexes. Text sorts by code point |
| Unauthenticated Patroni REST API | Basic auth on the REST API's write endpoints | Health checks stay open; switchover, restart and config changes need the password |
| Unencrypted repository | pgBackRest `aes-256-cbc` | Backups leave the private network |
| Netplan step for the private NIC | Not needed | Servers are created already attached to the network, so cloud-init configures the interface |
| | Read-only replica service on port 5433 | Free with a third node |

Kept as in the post: `ttl 30 / loop_wait 10 / retry_timeout 10`, `synchronous_mode: on` with `strict: off`, the watchdog with a 5 s safety margin, the load balancer health check (`/primary`, every 3 s, 2 retries), the PgBouncer settings and its `Restart=always` drop-in, the restart-on-demotion path unit, `RequiresMountsFor` on the data Volume, the backup timer that asks its own private address instead of localhost, and disabled unattended upgrades (patching a database node is a planned switchover, not a 6 a.m. surprise).

**Why the Hetzner Load Balancer and not HAProxy.** The load balancer is managed, costs €7.49 a month, needs no server of its own to patch or monitor, and is not a single point of failure you have to make redundant. Because it health-checks Patroni directly, it follows the leader with no API calls, floating IPs, keepalived or vip-manager. HAProxy would need its own pair of servers plus a floating IP and a script to move it, which costs more and adds a moving part to every failover. The trade-offs: a Hetzner load balancer cannot have a firewall attached, so this one has no public interface at all; its shortest health-check interval is 3 s; and it never closes established connections, which the restart-on-demotion unit handles. The Docker tests use HAProxy with the same check settings in its place.

## Local test and CI

`make test` runs the whole stack on your machine: three systemd containers built from Ubuntu 24.04 act as the servers, HAProxy plays the Hetzner Load Balancer with the same `/primary` health check, and [SeaweedFS](https://github.com/seaweedfs/seaweedfs) plays Object Storage over TLS. It provisions the cluster with the same Ansible roles, checks that a second run changes nothing, runs `failover-test`, takes a backup and runs `restore-test`. `make test-down` removes everything. It needs Docker and takes about 5 minutes.

GitHub Actions runs on every push: `terraform fmt` and `validate`, `tflint` with all rules, `ansible-lint` with the production profile, `yamllint`, `shellcheck`, and the full Docker test.

The containers share the host's kernel, so the test leaves out the watchdog, the data Volume and kernel settings; everything else, including ufw, runs as on a real server.

## Operations

```bash
make status
make psql
make provision
make failover-test
make backup
make restore-test
make down
```

On a node, as root:

```bash
patronictl -c /etc/patroni/config.yml list
patronictl -c /etc/patroni/config.yml switchover pg-ha --leader pg-ha-1 --candidate pg-ha-2
patronictl -c /etc/patroni/config.yml edit-config pg-ha
patronictl -c /etc/patroni/config.yml reinit pg-ha pg-ha-3
sudo -u postgres pgbackrest --stanza=pg-ha info
tail /var/log/pg-role-callback.log
```

To patch the operating system, do one node at a time: patch and reboot the replicas, then `switchover` away from the leader and patch it.

## Limitations

- **One location.** The placement group puts the nodes on different physical hosts, not in different data centers. If the whole location fails, the cluster is down until it returns or you restore from the backups in the other location.
- **No monitoring yet.** The blog post lists the exporters and alerts that matter (leader missing, sync lost, WAL archiving failing, backup stale, PgBouncer down); they are not part of v0.1.
- **Private-network traffic is not encrypted** between etcd members, to Patroni's REST API and to PgBouncer. PostgreSQL itself has TLS with a self-signed certificate (`sslmode=require`, not `verify-full`). Hetzner private networks are isolated per project.
- **Changing the number of nodes or replacing a node** after creation needs manual etcd member changes.
- **Major PostgreSQL upgrades** are not automated.
- **Ubuntu 24.04** is the only tested image.
- **Transaction pooling** breaks session state: no `LISTEN/NOTIFY`, session advisory locks or `SET` outside a transaction through port 6432. Use port 5432 for those clients.
- **Automatic security updates are off** on purpose; plan the rolling patch above.

## Roadmap

- Prometheus exporters and the alert rules from the blog post
- TLS for etcd and the Patroni REST API
- `make patch`: rolling OS updates with automatic switchovers
- Node replacement and resizing
- Nightly logical dumps next to the pgBackRest backups
- Creating replicas from pgBackRest instead of `pg_basebackup` for large databases

## License

[MIT](LICENSE). The design and its reasoning are explained step by step in the
[blog post](https://polymatx.dev/blog/posts/postgres-ha-patroni-hetzner/).
