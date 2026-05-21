#!/usr/bin/env bash
#
# init-firewall.sh
#
# Egress allowlist for the dev container. Run with sudo by postCreateCommand.
# Pattern from Anthropic's reference devcontainer, project-tuned with the
# eight scrape-target vendors plus PyPI / npm / GitHub / Anthropic API.
#
# Defaults: DROP all outbound. Then re-allow:
#   - DNS (port 53 to container resolver)
#   - HTTPS to allowlisted domains
#   - localhost / 127.0.0.0/8
#   - Docker bridge networks (so claude container can reach sibling postgres/redis)
#
# Inbound is left at default-DROP except for related/established.

set -euo pipefail

# --------------------- 0. Reset ---------------------
iptables -F
iptables -X
iptables -t nat -F
iptables -t nat -X
ipset destroy allowed-domains 2>/dev/null || true

# --------------------- 1. Default policies ---------------------
iptables -P INPUT DROP
iptables -P FORWARD DROP
iptables -P OUTPUT DROP

# Loopback
iptables -A INPUT  -i lo -j ACCEPT
iptables -A OUTPUT -o lo -j ACCEPT

# Established connections
iptables -A INPUT  -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# --------------------- 2. DNS ---------------------
# Container resolver (typically 127.0.0.11 in Docker user-defined networks).
# Allow DNS queries out and responses in explicitly — don't rely solely on
# conntrack ESTABLISHED/RELATED for UDP on all kernel/Docker versions.
iptables -A OUTPUT -p udp --dport 53 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 53 -j ACCEPT
iptables -A INPUT  -p udp --sport 53 -j ACCEPT
iptables -A INPUT  -p tcp --sport 53 -j ACCEPT

# --------------------- 3. Docker bridge networks (sibling containers) ---------------------
# Allow the container to reach other containers on the same Docker network
# (postgres, redis services from the project's docker-compose.yml).
for SUBNET in 172.16.0.0/12 192.168.0.0/16 10.0.0.0/8; do
  iptables -A OUTPUT -d "$SUBNET" -j ACCEPT
done

# --------------------- 4. Allowlist domains ---------------------
ipset create allowed-domains hash:net family inet hashsize 1024 maxelem 65536

ALLOWED_DOMAINS=(
  # Anthropic / Claude Code
  "api.anthropic.com"
  "statsig.anthropic.com"
  "console.anthropic.com"
  "claude.ai"
  "docs.claude.com"
  "platform.claude.com"
  "docs.anthropic.com"

  # Package registries
  "registry.npmjs.org"
  "registry.yarnpkg.com"
  "pypi.org"
  "files.pythonhosted.org"
  "pythonhosted.org"

  # Python tooling
  "astral.sh"
  "docs.astral.sh"

  # Git hosting
  "github.com"
  "api.github.com"
  "objects.githubusercontent.com"
  "raw.githubusercontent.com"
  "codeload.github.com"
  "gist.github.com"
  "gist.githubusercontent.com"

  # Debian / apt mirrors
  "deb.debian.org"
  "security.debian.org"
  "archive.ubuntu.com"
  "security.ubuntu.com"
  "ports.ubuntu.com"
  "download.docker.com"

  # Project documentation domains
  "docs.djangoproject.com"
  "django-celery-beat.readthedocs.io"
  "docs.celeryq.dev"
  "docs.timescale.com"
  "www.postgresql.org"
  "postgresql.org"
  "redis.io"
  "docs.pydantic.dev"
  "docs.pytest.org"
  "pytest-django.readthedocs.io"
  "mypy.readthedocs.io"
  "tenacity.readthedocs.io"

  # Scrape targets (Tier 1 & Tier 2 vendors per PRD §9)
  "api.runpod.io"
  "docs.runpod.io"
  "graphql-docs.runpod.io"
  "lambdalabs.com"
  "console.vast.ai"
  "vast.ai"
  "nebius.com"
  "coreweave.com"
  "crusoe.ai"
  "computeprices.com"

  # Hyperscaler API endpoints (M05.5)
  "pricing.us-east-1.amazonaws.com"
  "ec2.us-east-1.amazonaws.com"
  "ec2.us-east-2.amazonaws.com"
  "ec2.us-west-2.amazonaws.com"
  "cloudbilling.googleapis.com"
  "compute.googleapis.com"
  "prices.azure.com"
  "management.azure.com"
  "docs.aws.amazon.com"
  "cloud.google.com"
  "learn.microsoft.com"

  # ML / Inference reference
  "huggingface.co"
  "docs.vllm.ai"
  "docs.sglang.ai"
)

resolve_and_add() {
  local domain="$1"
  local ips
  ips=$(getent ahosts "$domain" 2>/dev/null | awk '{print $1}' | grep -E '^[0-9]+\.' | sort -u || true)
  if [[ -z "$ips" ]]; then
    echo "  warn: could not resolve $domain (skipping)"
    return
  fi
  while read -r ip; do
    [[ -n "$ip" ]] && ipset add allowed-domains "$ip" -exist
  done <<< "$ips"
  echo "  ok: $domain ($(echo "$ips" | wc -l | tr -d ' ') IPs)"
}

echo "Resolving allowlisted domains..."
for d in "${ALLOWED_DOMAINS[@]}"; do
  resolve_and_add "$d"
done

iptables -A OUTPUT -p tcp -m set --match-set allowed-domains dst --dport 443 -j ACCEPT
iptables -A OUTPUT -p tcp -m set --match-set allowed-domains dst --dport 80  -j ACCEPT

# --------------------- 5. Verification ---------------------
echo "Firewall ready. Testing egress..."

if curl -sS --max-time 5 -o /dev/null -w "%{http_code}" https://api.anthropic.com/ | grep -qE '^(2|3|4)'; then
  echo "  ok: api.anthropic.com reachable"
else
  echo "  WARN: api.anthropic.com not reachable — Claude Code may not work"
fi

# Negative test: random non-allowlisted IP should be unreachable.
if timeout 3 curl -sS -o /dev/null https://1.1.1.1/ 2>/dev/null; then
  echo "  WARN: egress firewall is NOT blocking unallowlisted traffic"
  exit 1
else
  echo "  ok: unallowlisted traffic is blocked"
fi

echo "Firewall initialization complete."
