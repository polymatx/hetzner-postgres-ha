output "nodes" {
  description = "Database nodes with their public and private addresses."
  value = {
    for name, server in hcloud_server.node : name => {
      id         = server.id
      public_ip  = server.ipv4_address
      private_ip = local.nodes[name].private_ip
    }
  }
}

output "load_balancer_ip" {
  description = "Private address of the load balancer. Applications connect here."
  value       = local.load_balancer_ip
}

output "endpoints" {
  description = "Connection endpoints on the load balancer, reachable from the private network."
  value = merge(
    {
      pgbouncer = "${local.load_balancer_ip}:6432"
      postgres  = "${local.load_balancer_ip}:5432"
    },
    var.replica_endpoint ? { replicas = "${local.load_balancer_ip}:5433" } : {}
  )
}

output "backup_bucket" {
  description = "Object Storage bucket used by pgBackRest, or null when backups are disabled."
  value       = local.backups_enabled ? local.backup_bucket : null
}

output "ansible_inventory" {
  description = "Ansible inventory for this cluster. The Makefile writes it to ansible/inventory/hosts.json."
  sensitive   = true
  value = {
    all = {
      vars = {
        ansible_user             = "root"
        ansible_ssh_common_args  = "-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=${abspath("${path.root}/../ansible/inventory/known_hosts")}"
        cluster_name             = var.cluster_name
        cluster_cidr             = var.subnet_cidr
        lb_host                  = local.load_balancer_ip
        pgbackrest_enabled       = local.backups_enabled
        pgbackrest_s3_bucket     = local.backup_bucket
        pgbackrest_s3_endpoint   = local.backup_s3_endpoint
        pgbackrest_s3_region     = var.backup_s3_region
        pgbackrest_s3_key        = var.backup_s3_access_key
        pgbackrest_s3_key_secret = var.backup_s3_secret_key
      }
      children = {
        postgres = {
          hosts = {
            for name, server in hcloud_server.node : name => {
              ansible_host     = server.ipv4_address
              node_private_ip  = local.nodes[name].private_ip
              hcloud_server_id = server.id
              pg_volume_device = var.volume_size > 0 ? "/dev/disk/by-id/scsi-0HC_Volume_${hcloud_volume.pgdata[name].id}" : ""
            }
          }
        }
      }
    }
  }
}
