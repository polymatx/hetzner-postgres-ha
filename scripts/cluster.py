#!/usr/bin/env python3
import argparse
import json
import os
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HCLOUD_API = "https://api.hetzner.cloud/v1"
PATRONI_CONFIG = "/etc/patroni/config.yml"


class Failure(Exception):
    pass


def log(message=""):
    print(message, flush=True)


class Node:
    def __init__(self, name, hostvars):
        self.name = name
        self.host = hostvars.get("ansible_host", name)
        self.ip = hostvars["node_private_ip"]
        self.server_id = hostvars.get("hcloud_server_id")
        self.user = hostvars.get("ansible_user", "root")
        self.docker = hostvars.get("ansible_connection", "ssh") in ("docker", "community.docker.docker")
        self.ssh_args = shlex.split(hostvars.get("ansible_ssh_common_args", ""))

    def command(self, script, tty=False):
        if self.docker:
            return ["docker", "exec"] + (["-it"] if tty else []) + [self.host, "bash", "-c", script]
        return (
            ["ssh"]
            + (["-t"] if tty else [])
            + self.ssh_args
            + [
                "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=5",
                "-o", "ServerAliveInterval=5",
                "-o", "ServerAliveCountMax=2",
                "-o", "ControlMaster=auto",
                "-o", "ControlPath=/tmp/hpha-%C",
                "-o", "ControlPersist=120s",
                f"{self.user}@{self.host}",
                "bash -c " + shlex.quote(script),
            ]
        )

    def run(self, script, check=True, timeout=120):
        try:
            result = subprocess.run(self.command(script), capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            if check:
                raise Failure(f"{self.name}: command timed out after {timeout}s")
            return subprocess.CompletedProcess([], 124, "", "timeout")
        if check and result.returncode != 0:
            raise Failure(f"{self.name}: command failed ({result.returncode}): {result.stderr.strip() or result.stdout.strip()}")
        return result

    def stream(self, script):
        return subprocess.Popen(self.command(script), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)


class Cluster:
    def __init__(self, inventory):
        try:
            raw = subprocess.run(["ansible-inventory", "-i", inventory, "--list"], capture_output=True, text=True, check=True).stdout
        except FileNotFoundError:
            raise Failure("ansible-inventory not found. Run make deps first.")
        except subprocess.CalledProcessError as error:
            raise Failure(f"cannot read inventory {inventory}: {error.stderr.strip()}")
        data = json.loads(raw)
        hostvars = data.get("_meta", {}).get("hostvars", {})
        names = data.get("postgres", {}).get("hosts", [])
        if not names:
            raise Failure(f"no hosts in group postgres in {inventory}. Run make up (or make test-up) first.")
        self.nodes = {name: Node(name, hostvars[name]) for name in names}
        first = hostvars[names[0]]
        self.name = first.get("cluster_name", "pg-ha")
        self.lb = first["lb_host"]
        self.db = first.get("app_db_name", "appdb")
        self.user = first.get("app_db_user", "appuser")
        self.backups = bool(first.get("pgbackrest_enabled", False))

    def state(self, via=None):
        candidates = [self.nodes[via]] if via else list(self.nodes.values())
        for node in candidates:
            result = node.run(f"curl -s --max-time 2 http://{node.ip}:8008/cluster", check=False, timeout=10)
            if result.returncode == 0 and result.stdout.strip():
                try:
                    return json.loads(result.stdout)
                except json.JSONDecodeError:
                    continue
        return None

    def members(self, via=None):
        state = self.state(via)
        return {m["name"]: m for m in state.get("members", [])} if state else {}

    def leader(self, members):
        return next((name for name, m in members.items() if m.get("role") in ("leader", "master", "primary")), None)

    def lb_psql(self, node, sql, port=5432):
        conninfo = f"host={self.lb} port={port} dbname={self.db} user={self.user} connect_timeout=5"
        return node.run(f"psql {shlex.quote(conninfo)} -XAtqc {shlex.quote(sql)}").stdout.strip()

    def local_psql(self, node, sql, db=None):
        return node.run(f"runuser -u postgres -- psql -XAtq -d {db or self.db} -c {shlex.quote(sql)}").stdout.strip()

    def power(self, node, action):
        if node.docker:
            subprocess.run(["docker", "kill" if action == "poweroff" else "start", node.host], capture_output=True, check=True)
            return
        started = self.hcloud("POST", f"/servers/{node.server_id}/actions/{action}")["action"]
        deadline = time.monotonic() + 120
        while started["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.5)
            started = self.hcloud("GET", f"/servers/actions/{started['id']}")["action"]
        if started["status"] != "success":
            raise Failure(f"Hetzner API {action} for {node.name} ended as {started['status']}: {started.get('error')}")

    def hcloud(self, method, path):
        token = os.environ.get("HCLOUD_TOKEN")
        if not token:
            raise Failure("HCLOUD_TOKEN is not set; it is needed to power servers off and on.")
        request = urllib.request.Request(
            f"{HCLOUD_API}{path}",
            method=method,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            data=b"{}" if method == "POST" else None,
        )
        try:
            return json.loads(urllib.request.urlopen(request, timeout=30).read())
        except urllib.error.HTTPError as error:
            raise Failure(f"Hetzner API {method} {path} failed: HTTP {error.code} {error.read().decode()[:200]}")


def print_members(members):
    log(f"  {'member':<14} {'role':<14} {'state':<11} {'TL':>3}  {'lag':>8}")
    for name in sorted(members):
        m = members[name]
        lag = m.get("lag", m.get("receive_lag", ""))
        log(f"  {name:<14} {m.get('role', ''):<14} {m.get('state', ''):<11} {m.get('timeline', ''):>3}  {str(lag):>8}")


def healthy(members, expected):
    return len(members) == expected and all(m.get("state") in ("running", "streaming") for m in members.values())


def cmd_status(cluster, args):
    node = next(iter(cluster.nodes.values()))
    log(node.run(f"patronictl -c {PATRONI_CONFIG} list").stdout.rstrip())
    log()
    log(f"Load balancer {cluster.lb}: 5432 postgres (leader), 6432 pgbouncer (leader), 5433 replicas")
    if cluster.backups:
        members = cluster.members()
        leader = cluster.nodes.get(cluster.leader(members) or node.name, node)
        log()
        log(leader.run(f"runuser -u postgres -- pgbackrest --stanza={cluster.name} info").stdout.rstrip())
    else:
        log("Backups: disabled")


class Writer:
    def __init__(self, cluster, node, run_id, port):
        self.records = []
        self.lock = threading.Lock()
        self.stop_file = f"/tmp/hpha-stop-{run_id}"
        self.node = node
        conninfo = f"host={cluster.lb} port={port} dbname={cluster.db} user={cluster.user} connect_timeout=2"
        insert = f"insert into failover_probe(run) values ('{run_id}') returning id"
        script = f"""
end=$((SECONDS + 900))
while [ ! -e {self.stop_file} ] && [ $SECONDS -lt $end ]; do
  if out=$(timeout 3 psql {shlex.quote(conninfo)} -XAtqc {shlex.quote(insert)} 2>&1); then
    echo "ok $out"
  else
    echo "fail $(printf '%s' "$out" | tr '\\n' ' ' | cut -c1-160)"
  fi
  sleep 0.2
done
rm -f {self.stop_file}
echo done
"""
        self.process = node.stream(script)
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        for line in self.process.stdout:
            line = line.strip()
            status, _, detail = line.partition(" ")
            if status in ("ok", "fail"):
                with self.lock:
                    self.records.append((time.monotonic(), status, detail))

    def snapshot(self):
        with self.lock:
            return list(self.records)

    def stop(self):
        self.node.run(f"touch {self.stop_file}", check=False)
        self.thread.join(timeout=30)
        self.process.wait(timeout=30)


def cmd_failover_test(cluster, args):
    members = cluster.members()
    expected = len(cluster.nodes)
    if not healthy(members, expected):
        print_members(members)
        raise Failure("the cluster is not healthy; every member must be running before the test")
    old_leader = cluster.leader(members)
    sync = next((n for n, m in members.items() if m.get("role") == "sync_standby"), None)
    replicas = [n for n in members if n != old_leader]
    writer_name = next((n for n in replicas if n != sync), replicas[0])
    writer_node = cluster.nodes[writer_name]
    run_id = time.strftime("ft-%Y%m%d-%H%M%S", time.gmtime())
    old_timeline = members[old_leader].get("timeline")

    log("Cluster before the test:")
    print_members(members)
    log()
    cluster.lb_psql(
        writer_node,
        "create table if not exists failover_probe (id bigserial primary key, run text not null, "
        "written_at timestamptz not null default clock_timestamp())",
    )
    log(f"Writing a row every 0.2 s from {writer_name} through the load balancer ({cluster.lb}:{args.port}).")
    writer = Writer(cluster, writer_node, run_id, args.port)

    deadline = time.monotonic() + 30
    while sum(1 for r in writer.snapshot() if r[1] == "ok") < 10:
        if time.monotonic() > deadline:
            writer.stop()
            raise Failure(f"writes through the load balancer are not working: {writer.snapshot()[-1:]}")
        time.sleep(0.2)

    acked_before = sum(1 for r in writer.snapshot() if r[1] == "ok")
    log(f"{acked_before} writes acknowledged. Powering off the leader {old_leader} now.")
    requested = time.monotonic()
    power = {}

    def power_off():
        try:
            cluster.power(cluster.nodes[old_leader], "poweroff")
            power["done"] = time.monotonic()
        except Failure as error:
            power["error"] = error

    threading.Thread(target=power_off, daemon=True).start()
    log("Waiting for a new leader and for writes to resume...")

    def outage(records):
        first_fail = next((r for r in records if r[0] >= requested and r[1] == "fail"), None)
        if not first_fail:
            return None, None, None
        last_ok = next((r for r in reversed(records) if r[1] == "ok" and r[0] < first_fail[0]), None)
        resumed = next((r for r in records if r[1] == "ok" and r[0] > first_fail[0]), None)
        return last_ok, first_fail, resumed

    new_leader = new_leader_at = None
    last_poll = 0.0
    while True:
        if "error" in power:
            writer.stop()
            raise power["error"]
        now = time.monotonic()
        if now - requested > args.timeout:
            writer.stop()
            raise Failure(f"no recovery within {args.timeout}s (new leader: {new_leader})")
        last_ok, first_fail, resumed = outage(writer.snapshot())
        if new_leader is None and now - last_poll >= 1:
            last_poll = now
            leader = cluster.leader(cluster.members(via=writer_name))
            if leader and leader != old_leader:
                new_leader, new_leader_at = leader, time.monotonic()
        if new_leader and resumed and "done" in power and time.monotonic() - max(new_leader_at, resumed[0]) > 5:
            break
        time.sleep(0.2)

    writer.stop()
    records = writer.snapshot()
    last_ok, first_fail, resumed = outage(records)
    t0 = last_ok[0] if last_ok else requested
    oks = [r for r in records if r[1] == "ok"]
    fails = [r for r in records if r[1] == "fail"]
    acked_ids = {int(r[2].split()[0]) for r in oks}
    stored = cluster.lb_psql(writer_node, f"select id from failover_probe where run = '{run_id}'")
    stored_ids = {int(x) for x in stored.split()} if stored else set()
    lost = sorted(acked_ids - stored_ids)

    log()
    log(f"Timeline (0 s = the last write {old_leader} acknowledged):")
    log(f"[{0.0:6.1f}s] last acknowledged write before the outage")
    log(f"[{first_fail[0] - t0:6.1f}s] first failed write: {first_fail[2][:90]}")
    log(f"[{new_leader_at - t0:6.1f}s] {new_leader} is the new leader")
    log(f"[{resumed[0] - t0:6.1f}s] writes are succeeding again")
    log()
    log(f"Powering {old_leader} back on and waiting for it to rejoin as a replica...")
    cluster.power(cluster.nodes[old_leader], "poweron")
    t_on = time.monotonic()
    rejoined_at = None
    while time.monotonic() - t_on < args.rejoin_timeout:
        current = cluster.members(via=new_leader)
        m = current.get(old_leader)
        if m and m.get("role") in ("replica", "sync_standby") and m.get("state") in ("streaming", "running"):
            rejoined_at = time.monotonic()
            break
        time.sleep(2)
    final = cluster.members(via=new_leader)
    callback = ""
    if rejoined_at:
        callback = cluster.nodes[old_leader].run("tail -n 1 /var/log/pg-role-callback.log 2>/dev/null", check=False).stdout.strip()

    log()
    log("Cluster after the test:")
    print_members(final)
    log()
    log("Result")
    log(f"  killed leader          {old_leader} (timeline {old_timeline})")
    log(f"  new leader             {new_leader}, {new_leader_at - t0:.1f} s after the outage began")
    log(f"  writes resumed after   {resumed[0] - t0:.1f} s")
    log(f"  writes                 {len(oks)} acknowledged, {len(fails)} failed")
    log(f"  acknowledged but lost  {len(lost)}")
    if rejoined_at:
        log(f"  old leader rejoined    after {rejoined_at - t_on:.1f} s as {final[old_leader].get('role')}")
    else:
        log(f"  old leader rejoined    no (not back within {args.rejoin_timeout}s)")
    if callback:
        log(f"  pgbouncer reconciler   {callback}")

    problems = []
    if lost:
        problems.append(f"{len(lost)} acknowledged writes are missing: {lost[:10]}")
    if not rejoined_at:
        problems.append(f"{old_leader} did not rejoin")
    if problems:
        raise Failure("; ".join(problems))
    log()
    log("PASS: the cluster failed over, writes continued and no acknowledged write was lost.")


def cmd_backup(cluster, args):
    if not cluster.backups:
        raise Failure("backups are disabled for this cluster")
    leader = cluster.nodes[cluster.leader(cluster.members())]
    log(f"Running a {args.type} backup on the leader {leader.name}...")
    result = leader.run(f"/usr/local/bin/pgbackrest-backup {args.type}", timeout=3600)
    log("\n".join(line[:200] for line in result.stdout.rstrip().splitlines()[-6:]))
    log(leader.run(f"runuser -u postgres -- pgbackrest --stanza={cluster.name} info").stdout.rstrip())


def cmd_restore_test(cluster, args):
    if not cluster.backups:
        raise Failure("backups are disabled for this cluster")
    members = cluster.members()
    leader_name = cluster.leader(members)
    if not leader_name:
        raise Failure("the cluster has no leader")
    leader = cluster.nodes[leader_name]
    sync = next((n for n, m in members.items() if m.get("role") == "sync_standby"), None)
    others = [n for n in members if n != leader_name]
    target = cluster.nodes[next((n for n in others if n != sync), others[0] if others else leader_name)]
    run_id = time.strftime("rt%Y%m%d%H%M%S", time.gmtime())
    stanza = cluster.name

    info = json.loads(leader.run(f"runuser -u postgres -- pgbackrest --stanza={stanza} --output=json info").stdout)
    if not info or not info[0].get("backup"):
        log("No backup yet; taking a full backup first...")
        leader.run("/usr/local/bin/pgbackrest-backup full", timeout=3600)

    log(f"Writing marker rows on the leader {leader_name}...")
    cluster.local_psql(leader, "create table if not exists restore_probe (label text primary key, at timestamptz not null default clock_timestamp())")
    cluster.local_psql(leader, f"insert into restore_probe(label) values ('before-{run_id}')")
    time.sleep(1.5)
    target_time = cluster.local_psql(leader, "select to_char(clock_timestamp() at time zone 'UTC', 'YYYY-MM-DD HH24:MI:SS.US') || '+00'")
    time.sleep(1.5)
    cluster.local_psql(leader, f"insert into restore_probe(label) values ('after-{run_id}')")
    leader.run(f"runuser -u postgres -- pgbackrest --stanza={stanza} check", timeout=600)
    log(f"  row before-{run_id} committed before {target_time}")
    log(f"  row after-{run_id}  committed after it; both are archived")

    data_dir = f"/var/lib/postgresql/restore-test-{run_id}"
    script = f"""
set -euo pipefail
pg_ctl=$(ls -d /usr/lib/postgresql/*/bin/pg_ctl | sort -V | tail -n 1)
rm -rf {data_dir} {data_dir}.log
runuser -u postgres -- pgbackrest --stanza={stanza} --pg1-path={data_dir} --type=time --target={shlex.quote(target_time)} \
  --target-action=promote --archive-mode=off --log-level-console=warn restore
printf 'local all all peer\\n' > {data_dir}/pg_hba.restore.conf
chown postgres:postgres {data_dir}/pg_hba.restore.conf
runuser -u postgres -- "$pg_ctl" -D {data_dir} -l {data_dir}.log -w -t 900 \
  -o "-c port=5499 -c listen_addresses='' -c unix_socket_directories=/tmp -c archive_mode=off -c synchronous_standby_names='' -c shared_buffers=128MB -c cluster_name=restore-test -c hba_file={data_dir}/pg_hba.restore.conf" start >/dev/null
for i in $(seq 1 300); do
  [ "$(runuser -u postgres -- psql -h /tmp -p 5499 -XAtqc 'select pg_is_in_recovery()' postgres 2>/dev/null)" = f ] && break
  sleep 2
done
runuser -u postgres -- psql -h /tmp -p 5499 -XAtqc "select label from restore_probe where label like '%-{run_id}' order by label" {cluster.db}
"""
    cleanup = f"""
pg_ctl=$(ls -d /usr/lib/postgresql/*/bin/pg_ctl | sort -V | tail -n 1)
runuser -u postgres -- "$pg_ctl" -D {data_dir} -m fast stop >/dev/null 2>&1 || true
rm -rf {data_dir} {data_dir}.log
"""
    log()
    log(f"Restoring the latest backup plus WAL to {target_time} on {target.name} (port 5499, separate data directory)...")
    started = time.monotonic()
    try:
        result = target.run(script, check=False, timeout=3600)
    finally:
        target.run(cleanup, check=False, timeout=300)
    took = time.monotonic() - started
    if result.returncode != 0:
        raise Failure(f"restore failed: {result.stderr.strip()[-1500:] or result.stdout.strip()[-1500:]}")
    labels = result.stdout.split()
    log(f"  restored and promoted in {took:.1f} s; marker rows found: {', '.join(labels) or 'none'}")
    if labels != [f"before-{run_id}"]:
        raise Failure(f"expected only before-{run_id} in the restored database, found {labels}")
    log()
    log("PASS: point-in-time restore returned the row written before the target and not the one written after it.")


def cmd_psql(cluster, args):
    node = next(iter(cluster.nodes.values()))
    conninfo = f"host={cluster.lb} port={args.port} dbname={cluster.db} user={cluster.user}"
    command = node.command(f"psql {shlex.quote(conninfo)}", tty=True)
    os.execvp(command[0], command)


def main():
    parser = argparse.ArgumentParser(description="Operate and test a hetzner-postgres-ha cluster.")
    parser.add_argument("-i", "--inventory", default=os.environ.get("INVENTORY", "ansible/inventory/hosts.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show members, endpoints and backups")
    failover = sub.add_parser("failover-test", help="power off the leader under writes and check the cluster recovers")
    failover.add_argument("--port", type=int, default=6432, help="load balancer port the writer uses (6432 PgBouncer, 5432 PostgreSQL)")
    failover.add_argument("--timeout", type=int, default=180)
    failover.add_argument("--rejoin-timeout", type=int, default=300)
    backup = sub.add_parser("backup", help="run a pgBackRest backup on the leader")
    backup.add_argument("--type", choices=["full", "diff", "incr"], default="full")
    sub.add_parser("restore-test", help="point-in-time restore into a scratch data directory and verify it")
    psql = sub.add_parser("psql", help="open psql through the load balancer")
    psql.add_argument("--port", type=int, default=6432)
    args = parser.parse_args()

    handlers = {
        "status": cmd_status,
        "failover-test": cmd_failover_test,
        "backup": cmd_backup,
        "restore-test": cmd_restore_test,
        "psql": cmd_psql,
    }
    try:
        handlers[args.command](Cluster(args.inventory), args)
    except Failure as error:
        log(f"FAIL: {error}")
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
