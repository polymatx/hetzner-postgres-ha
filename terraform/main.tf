locals {
  network_zones = {
    fsn1 = "eu-central"
    nbg1 = "eu-central"
    hel1 = "eu-central"
    ash  = "us-east"
    hil  = "us-west"
    sin  = "ap-southeast"
  }

  labels = merge(var.labels, {
    cluster    = var.cluster_name
    managed-by = "hetzner-postgres-ha"
  })

  nodes = {
    for i in range(var.node_count) : "${var.cluster_name}-${i + 1}" => {
      index      = i
      private_ip = cidrhost(var.subnet_cidr, 21 + i)
    }
  }

  load_balancer_ip = cidrhost(var.subnet_cidr, 10)

  admin_cidrs = length(var.admin_cidrs) > 0 ? var.admin_cidrs : ["${chomp(data.http.my_ip[0].response_body)}/32"]

  ssh_keys = concat([for k in hcloud_ssh_key.admin : k.id], var.ssh_key_names)

  backups_enabled    = nonsensitive(var.backup_s3_access_key != "" && var.backup_s3_secret_key != "")
  backup_bucket      = var.backup_bucket != "" ? var.backup_bucket : "${var.cluster_name}-pgbackrest-${random_id.bucket_suffix.hex}"
  backup_s3_endpoint = var.backup_s3_endpoint != "" ? var.backup_s3_endpoint : "${var.backup_s3_region}.your-objectstorage.com"
}

data "http" "my_ip" {
  count = length(var.admin_cidrs) > 0 ? 0 : 1
  url   = "https://api.ipify.org"

  lifecycle {
    postcondition {
      condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$", chomp(self.response_body)))
      error_message = "Could not detect your public IPv4 address. Set admin_cidrs explicitly."
    }
  }
}

resource "random_id" "bucket_suffix" {
  byte_length = 4
}

resource "hcloud_ssh_key" "admin" {
  count      = length(var.ssh_public_keys)
  name       = "${var.cluster_name}-admin-${count.index + 1}"
  public_key = var.ssh_public_keys[count.index]
  labels     = local.labels
}

resource "hcloud_network" "this" {
  name     = var.cluster_name
  ip_range = var.network_cidr
  labels   = local.labels
}

resource "hcloud_network_subnet" "this" {
  network_id   = hcloud_network.this.id
  type         = "cloud"
  network_zone = local.network_zones[var.location]
  ip_range     = var.subnet_cidr
}

resource "hcloud_placement_group" "this" {
  name   = var.cluster_name
  type   = "spread"
  labels = local.labels
}

resource "hcloud_firewall" "this" {
  name   = var.cluster_name
  labels = local.labels

  rule {
    description = "SSH from admin networks"
    direction   = "in"
    protocol    = "tcp"
    port        = "22"
    source_ips  = local.admin_cidrs
  }

  rule {
    description = "ICMP"
    direction   = "in"
    protocol    = "icmp"
    source_ips  = ["0.0.0.0/0", "::/0"]
  }
}

resource "hcloud_server" "node" {
  for_each = local.nodes

  name               = each.key
  server_type        = var.server_type
  image              = var.image
  location           = var.location
  ssh_keys           = local.ssh_keys
  placement_group_id = hcloud_placement_group.this.id
  firewall_ids       = [hcloud_firewall.this.id]
  labels             = merge(local.labels, { role = "postgres" })

  public_net {
    ipv4_enabled = true
    ipv6_enabled = true
  }

  network {
    network_id = hcloud_network.this.id
    ip         = each.value.private_ip
  }

  depends_on = [hcloud_network_subnet.this]

  lifecycle {
    ignore_changes = [image, ssh_keys]

    precondition {
      condition     = length(local.ssh_keys) > 0
      error_message = "Set ssh_public_keys or ssh_key_names, otherwise nobody can log in to the nodes."
    }
  }
}

resource "hcloud_volume" "pgdata" {
  for_each = var.volume_size > 0 ? local.nodes : {}

  name     = "${each.key}-pgdata"
  size     = var.volume_size
  location = var.location
  format   = "ext4"
  labels   = local.labels
}

resource "hcloud_volume_attachment" "pgdata" {
  for_each = hcloud_volume.pgdata

  volume_id = each.value.id
  server_id = hcloud_server.node[each.key].id
  automount = false
}
