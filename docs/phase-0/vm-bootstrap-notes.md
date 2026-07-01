# Phase 0 Azure VM Bootstrap and Validation Notes

This document records Workstream B evidence for the existing Azure Linux development VM used by the Phase 0 setup, access, and sample-product spike.

## VM identity

| Item | Value |
| --- | --- |
| SSH alias | `akasha-staging` |
| Hostname | `akasha-staging` |
| Admin user observed | `akashaadmin` |
| OS | Ubuntu 24.04.4 LTS |
| Kernel | Azure Ubuntu kernel `6.17.0-1018-azure` |
| Observed outbound egress IP | `20.219.3.35` |

The observed egress IP is the value to use for provider-side whitelisting checks unless Azure networking later routes outbound traffic through another NAT/Public IP resource.

## Runtime validation

| Check | Result |
| --- | --- |
| SSH key-based access | Passed |
| Password SSH authentication | Disabled |
| Docker Engine | Installed: `Docker version 29.5.3` |
| Docker Compose plugin | Installed: `Docker Compose version v5.1.4` |
| Docker service | Active |
| Docker data root | `/srv/akasha/runtime/docker` |
| Docker Compose runtime test | Passed with `hello-world` compose project |
| Phase 0 directory setup | Created under `/srv/akasha` |

## Storage layout

| Mount | Size | Used | Available | Notes |
| --- | ---: | ---: | ---: | --- |
| `/` | 247 GB | 4.7 GB | 243 GB | OS disk |
| `/srv/akasha` | 503 GB | 51 GB | 427 GB | Data disk for Akasha runtime/sample storage |

Current observations:

- A dedicated `/srv/akasha` data disk is mounted and Docker is already configured to store runtime data under it.
- No separate `/scratch`, `/data`, or `/mnt/scratch` mount was observed during validation.
- Phase 0 paths were created on `/srv/akasha`:
  - `/srv/akasha/raw-samples`
  - `/srv/akasha/scratch`
  - `/srv/akasha/logs`
- For Phase 0 sample processing, use `/srv/akasha/raw-samples` for retained downloaded samples and `/srv/akasha/scratch` for temporary extraction/inspection unless a separate scratch disk is added.

## Firewall and exposed ports

UFW is active with default deny incoming and allow outgoing.

Observed allowed inbound rules:

| Port | Scope | Notes |
| --- | --- | --- |
| `22/tcp` | Anywhere, IPv4/IPv6 | Commented as temporary public SSH for Azure rehearsal |
| `80/tcp` | Anywhere, IPv4/IPv6 | Commented as public HTTP for Coolify web route |
| `443/tcp` | Anywhere, IPv4/IPv6 | Commented as public HTTPS for Coolify web route |
| `8888/tcp` | Anywhere, IPv4/IPv6 | No comment observed |

Observed listeners also included services bound to `0.0.0.0` on ports `22`, `80`, `443`, `8080`, and `8888`, plus several localhost-only listeners.

Security note:

- SSH is key-only, but `22/tcp` is currently open to the public internet. For Phase 0, restrict SSH to the team VPN/corporate/admin CIDR if operationally possible.
- Do not expose Postgres, Redis, MinIO, provider secrets, metrics exporters, or future internal services publicly.
- Confirm whether `8888` and `8080` are intentionally exposed before running provider samples or storing credentials on the VM.

## Monitoring

| Check | Result |
| --- | --- |
| `prometheus-node-exporter` service | Active and enabled |
| `127.0.0.1:9100/metrics` | Reachable |
| Listener binding | `127.0.0.1:9100` only |

Basic monitoring exporter setup is complete for Phase 0 local validation. If Prometheus later runs in Docker and needs to scrape the host exporter, revisit the bind address and Docker networking during Phase 1.

## Provider network reachability

Unauthenticated HTTPS reachability from the VM was tested without storing credentials.

| Provider endpoint | Result |
| --- | --- |
| `https://bhoonidhi.nrsc.gov.in` | HTTP `200` |
| `https://catalogue.dataspace.copernicus.eu` | HTTP `404` from reachable host; endpoint is reachable but this is not an auth/search validation |
| `https://m2m.cr.usgs.gov` | HTTP `200` |
| `https://urs.earthdata.nasa.gov` | HTTP `200` |

These checks validate DNS/TLS/network egress only. Provider authentication, search, ordering, download links, checksums, and quotas remain Workstream C.

## Workstream B gaps

| Gap | Impact | Recommended action |
| --- | --- | --- |
| No separate scratch mount observed | Scratch/data separation is weaker than planned | Use `/srv/akasha/scratch` for Phase 0 or attach/mount a separate scratch disk |
| SSH public from anywhere | Higher exposure than recommended | Restrict `22/tcp` to approved admin CIDR/VPN if possible |
| Port `8888` public and `8080` listening | Unknown exposure | Confirm intended services before storing provider credentials |
| Azure NSG not independently exported | Host firewall checked, cloud firewall not yet recorded | Capture Azure NSG rules from Azure Portal/CLI |

## Validation commands used

```bash
hostname
uname -a
lsb_release -ds
curl -fsS --max-time 15 https://ifconfig.me
docker --version
docker compose version
systemctl is-active docker
docker info --format 'DockerRootDir={{.DockerRootDir}} ServerVersion={{.ServerVersion}}'
docker compose -p akasha-phase0-check up --abort-on-container-exit --quiet-pull
df -hT
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINTS
sudo -n sshd -T | grep -Ei 'passwordauthentication|permitrootlogin|pubkeyauthentication'
sudo -n ufw status verbose
systemctl is-active node_exporter
curl -fsS --max-time 5 http://localhost:9100/metrics
sudo apt-get install -y prometheus-node-exporter
systemctl is-active prometheus-node-exporter
curl -fsS --max-time 5 http://127.0.0.1:9100/metrics
curl -k -L -sS -o /dev/null -w 'bhoonidhi %{http_code} %{remote_ip} %{time_total}\n' --max-time 20 https://bhoonidhi.nrsc.gov.in
curl -L -sS -o /dev/null -w 'cdse_catalogue %{http_code} %{remote_ip} %{time_total}\n' --max-time 20 https://catalogue.dataspace.copernicus.eu
curl -L -sS -o /dev/null -w 'usgs_m2m %{http_code} %{remote_ip} %{time_total}\n' --max-time 20 https://m2m.cr.usgs.gov
curl -L -sS -o /dev/null -w 'earthdata_urs %{http_code} %{remote_ip} %{time_total}\n' --max-time 20 https://urs.earthdata.nasa.gov
```

## Phase 1 Ansible conversion notes

Convert these manual validation/setup items into Ansible during Phase 1:

1. Base package updates and security patching.
2. SSH hardening and admin-user policy.
3. UFW/NSG rule documentation and enforcement.
4. Docker Engine and Compose plugin installation.
5. Docker data-root configuration under `/srv/akasha/runtime/docker`.
6. `/srv/akasha` data layout creation:
   - `raw-samples/`
   - `scratch/`
   - `runtime/docker/`
   - `logs/`
7. Node exporter installation and private binding.
8. Provider egress validation tasks.
9. Disk usage and alert thresholds.
