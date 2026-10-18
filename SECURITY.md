# Security Policy

## Model

hetzner-postgres-ha builds infrastructure in your own Hetzner Cloud project. What it is meant
to guarantee:

- PostgreSQL, PgBouncer, Patroni and etcd are reachable only from the private network. The
  load balancer has no public interface, the cloud firewall allows only SSH from
  `admin_cidrs` and ICMP, and ufw on every node repeats the same rules.
- Passwords are generated on your machine, stored with mode 600 in
  `ansible/inventory/.secrets/` (ignored by git) and on the nodes only in files readable by
  root or postgres. Database authentication uses SCRAM.
- Backups are encrypted client-side before they leave the private network.
- Patroni's REST API accepts state-changing requests only with its password.

Known gaps are listed under Limitations in the README: traffic inside the private network
between etcd members, to Patroni's REST API and to PgBouncer is not encrypted, and
PostgreSQL uses a self-signed certificate.

## Reporting a vulnerability

If you find something that breaks that model, for example a port reachable from the
internet, a secret written somewhere world-readable or logged, a `pg_hba` rule broader than
documented, or a way to change the cluster through the REST API without the password,
please write to **farid.vosoughi.65@gmail.com** instead of opening a public issue. You'll get
a response within a few days, and credit in the release notes if you want it.
