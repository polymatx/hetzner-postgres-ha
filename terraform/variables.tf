variable "cluster_name" {
  description = "Name of the cluster. Used as the Patroni scope, the pgBackRest stanza and the prefix of every Hetzner resource."
  type        = string
  default     = "pg-ha"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}[a-z0-9]$", var.cluster_name))
    error_message = "cluster_name must be 3-32 characters of lowercase letters, digits and dashes, starting with a letter."
  }
}

variable "location" {
  description = "Hetzner location for the servers and the load balancer (fsn1, nbg1, hel1, ash, hil, sin)."
  type        = string
  default     = "hel1"

  validation {
    condition     = contains(["fsn1", "nbg1", "hel1", "ash", "hil", "sin"], var.location)
    error_message = "location must be one of fsn1, nbg1, hel1, ash, hil, sin."
  }
}

variable "node_count" {
  description = "Number of database nodes. Each runs etcd, Patroni/PostgreSQL and PgBouncer. Must be odd so etcd keeps a quorum."
  type        = number
  default     = 3

  validation {
    condition     = contains([3, 5], var.node_count)
    error_message = "node_count must be 3 or 5."
  }
}

variable "server_type" {
  description = "Hetzner server type for the database nodes."
  type        = string
  default     = "cpx22"
}

variable "image" {
  description = "Operating system image for the database nodes. The Ansible roles target Ubuntu 24.04."
  type        = string
  default     = "ubuntu-24.04"
}

variable "volume_size" {
  description = "Size in GB of the Hetzner Volume that holds PGDATA on each node. Set to 0 to keep PGDATA on the server's local disk."
  type        = number
  default     = 20

  validation {
    condition     = var.volume_size == 0 || (var.volume_size >= 10 && var.volume_size <= 10240)
    error_message = "volume_size must be 0 or between 10 and 10240."
  }
}

variable "network_cidr" {
  description = "IP range of the private network."
  type        = string
  default     = "10.0.0.0/16"
}

variable "subnet_cidr" {
  description = "Subnet inside network_cidr for the nodes and the load balancer. Nodes get .21, .22, ... and the load balancer gets .10."
  type        = string
  default     = "10.0.0.0/24"
}

variable "ssh_public_keys" {
  description = "SSH public keys (file contents, not paths) to upload to the Hetzner project and install for root on every node."
  type        = list(string)
  default     = []
}

variable "ssh_key_names" {
  description = "Names of SSH keys that already exist in the Hetzner project, installed in addition to ssh_public_keys."
  type        = list(string)
  default     = []
}

variable "admin_cidrs" {
  description = "Source ranges allowed to reach SSH on the nodes' public interfaces. Empty means only the public IPv4 address of the machine running Terraform."
  type        = list(string)
  default     = []
}

variable "load_balancer_type" {
  description = "Hetzner load balancer type."
  type        = string
  default     = "lb11"
}

variable "replica_endpoint" {
  description = "Add a read-only service on port 5433 of the load balancer that spreads connections over the replicas."
  type        = bool
  default     = true
}

variable "backup_s3_region" {
  description = "Hetzner Object Storage location for pgBackRest (fsn1, nbg1 or hel1). Pick a different one from location so backups survive the loss of a site."
  type        = string
  default     = "fsn1"
}

variable "backup_s3_endpoint" {
  description = "S3 endpoint for pgBackRest. Empty means the Hetzner Object Storage endpoint of backup_s3_region."
  type        = string
  default     = ""
}

variable "backup_bucket" {
  description = "Bucket for pgBackRest. Empty means <cluster_name>-pgbackrest-<random suffix>. Ansible creates it if it does not exist."
  type        = string
  default     = ""
}

variable "backup_s3_access_key" {
  description = "S3 access key for Hetzner Object Storage. Leave empty to deploy without backups."
  type        = string
  default     = ""
  sensitive   = true
}

variable "backup_s3_secret_key" {
  description = "S3 secret key for Hetzner Object Storage."
  type        = string
  default     = ""
  sensitive   = true
}

variable "labels" {
  description = "Extra labels for every Hetzner resource."
  type        = map(string)
  default     = {}
}
