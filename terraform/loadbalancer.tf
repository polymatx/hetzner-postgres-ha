locals {
  lb_services = merge(
    {
      postgres  = { listen_port = 5432, destination_port = 5432, health_path = "/primary" }
      pgbouncer = { listen_port = 6432, destination_port = 6432, health_path = "/primary" }
    },
    var.replica_endpoint ? {
      replicas = { listen_port = 5433, destination_port = 5432, health_path = "/replica" }
    } : {}
  )
}

resource "hcloud_load_balancer" "this" {
  name               = var.cluster_name
  load_balancer_type = var.load_balancer_type
  location           = var.location
  labels             = local.labels

  algorithm {
    type = "round_robin"
  }
}

resource "hcloud_load_balancer_network" "this" {
  load_balancer_id        = hcloud_load_balancer.this.id
  subnet_id               = hcloud_network_subnet.this.id
  ip                      = local.load_balancer_ip
  enable_public_interface = false
}

resource "hcloud_load_balancer_target" "node" {
  for_each = hcloud_server.node

  type             = "server"
  load_balancer_id = hcloud_load_balancer.this.id
  server_id        = each.value.id
  use_private_ip   = true

  depends_on = [hcloud_load_balancer_network.this]
}

resource "hcloud_load_balancer_service" "this" {
  for_each = local.lb_services

  load_balancer_id = hcloud_load_balancer.this.id
  protocol         = "tcp"
  listen_port      = each.value.listen_port
  destination_port = each.value.destination_port
  proxyprotocol    = false

  health_check {
    protocol = "http"
    port     = 8008
    interval = 3
    timeout  = 2
    retries  = 2

    http {
      path         = each.value.health_path
      status_codes = ["200"]
      tls          = false
    }
  }
}
