# syntax=docker/dockerfile:1
#
# Dev container for llm-inference-pricing.
# Pattern: Anthropic's reference devcontainer + project-specific tooling.
#
# Run `claude --dangerously-skip-permissions` inside this container.
# Host filesystem and network are isolated; the egress firewall (init-firewall.sh)
# limits outbound traffic to allowlisted domains.

FROM node:24-bookworm

ARG TZ=Etc/UTC
ENV TZ="$TZ"
ENV DEBIAN_FRONTEND=noninteractive

# ---- System tooling ---------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
      # Build essentials
      build-essential \
      ca-certificates \
      curl \
      wget \
      gnupg \
      git \
      git-lfs \
      openssh-client \
      sudo \
      tzdata \
      unzip \
      zip \
      # Shell QoL
      zsh \
      less \
      vim \
      nano \
      jq \
      yq \
      tree \
      htop \
      ripgrep \
      fd-find \
      # Network / firewall
      iptables \
      ipset \
      dnsutils \
      iputils-ping \
      net-tools \
      # Postgres client (for psql against the project's TimescaleDB)
      postgresql-client \
      # Redis client (for redis-cli)
      redis-tools \
      # Python build deps (Python itself installed via pyenv-equivalent below)
      python3 \
      python3-pip \
      python3-venv \
      python3-dev \
      libpq-dev \
   && rm -rf /var/lib/apt/lists/*

# ---- uv (modern Python package manager) -------------------------------------
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

# ---- Docker CLI (for talking to host docker daemon via socket mount) --------
RUN install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/debian/gpg \
      | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
 && chmod a+r /etc/apt/keyrings/docker.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
      https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
      > /etc/apt/sources.list.d/docker.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
 && rm -rf /var/lib/apt/lists/*

# ---- Non-root user setup ----------------------------------------------------
# node user comes from base image with UID 1000.
ARG USERNAME=node
ARG USER_UID=1000
ARG USER_GID=$USER_UID

# Persist shell history across rebuilds (volume-mounted in devcontainer.json)
RUN mkdir -p /commandhistory \
 && touch /commandhistory/.zsh_history \
 && chown -R $USERNAME:$USERNAME /commandhistory

# Sudoers (no password) — needed for init-firewall.sh
RUN echo "$USERNAME ALL=(root) NOPASSWD:/usr/local/bin/init-firewall.sh" \
      > /etc/sudoers.d/$USERNAME-firewall \
 && chmod 0440 /etc/sudoers.d/$USERNAME-firewall

# Add node user to docker group so it can talk to mounted socket
RUN groupadd -f docker && usermod -aG docker $USERNAME

# ---- Persistent caches/configs (mounted as volumes in devcontainer.json) ----
RUN mkdir -p /home/$USERNAME/.claude \
             /home/$USERNAME/.cache \
             /home/$USERNAME/.local/share \
 && chown -R $USERNAME:$USERNAME /home/$USERNAME

# ---- Claude Code CLI (global install) ---------------------------------------
RUN npm install -g @anthropic-ai/claude-code

# ---- Firewall init script (executed by postCreateCommand) -------------------
COPY init-firewall.sh /usr/local/bin/init-firewall.sh
RUN chmod 0755 /usr/local/bin/init-firewall.sh

# ---- Python 3.12 via uv (Debian bookworm ships 3.11) ------------------------
# Runs as root so uv can place the managed Python in /root/.local; a symlink
# makes it available to all users. The node-user uv step below also gains it.
RUN uv python install 3.12

# ---- Default user + shell ---------------------------------------------------
USER $USERNAME
WORKDIR /workspace

ENV SHELL=/bin/zsh
ENV CLAUDE_CONFIG_DIR=/home/$USERNAME/.claude

# Inside the container use .venv-linux to avoid clobbering the host macOS .venv.
# The host's .venv contains arm64 Mach-O binaries; this one contains ELF/Linux.
ENV VIRTUAL_ENV=/workspace/.venv-linux
ENV PATH="/workspace/.venv-linux/bin:$PATH"

# Persist zsh history + activate container venv
RUN echo 'export HISTFILE=/commandhistory/.zsh_history' >> /home/$USERNAME/.zshrc \
 && echo 'export HISTSIZE=10000' >> /home/$USERNAME/.zshrc \
 && echo 'export SAVEHIST=10000' >> /home/$USERNAME/.zshrc \
 && echo 'setopt INC_APPEND_HISTORY SHARE_HISTORY' >> /home/$USERNAME/.zshrc \
 && echo 'export VIRTUAL_ENV=/workspace/.venv-linux' >> /home/$USERNAME/.zshrc \
 && echo 'export PATH="/workspace/.venv-linux/bin:$PATH"' >> /home/$USERNAME/.zshrc

# Block `git commit --no-verify` (pre-commit bypass)
RUN cat >> /home/$USERNAME/.zshrc <<'EOF'
git() {
  if [[ "$1" == "commit" ]]; then
    for arg in "$@"; do
      if [[ "$arg" == "--no-verify" || "$arg" == "-n" ]]; then
        echo "refusing: --no-verify bypasses pre-commit hooks."
        return 1
      fi
    done
  fi
  command git "$@"
}
EOF

CMD ["sleep", "infinity"]
