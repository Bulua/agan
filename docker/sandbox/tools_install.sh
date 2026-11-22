#!/usr/bin/env bash
set -euo pipefail

echo "[*] Installing specialized CTF tools..."

# 1. one_gadget (Ruby gem for Pwn glibc exploits)
gem install --no-document one_gadget || echo "[!] Warning: one_gadget install failed, continuing"

# 2. checksec wrapper (pwntools provides `checksec` CLI, ensure binary exists in PATH)
if ! command -v checksec >/dev/null 2>&1; then
    curl -fsSL https://raw.githubusercontent.com/slimm609/checksec.sh/main/checksec -o /usr/local/bin/checksec \
        && chmod +x /usr/local/bin/checksec || true
fi

# 3. ffuf (fast web fuzzer)
FFUF_VERSION="2.1.0"
curl -fsSL "https://github.com/ffuf/ffuf/releases/download/v${FFUF_VERSION}/ffuf_${FFUF_VERSION}_linux_amd64.tar.gz" \
    -o /tmp/ffuf.tar.gz && \
    tar -xzf /tmp/ffuf.tar.gz -C /usr/local/bin ffuf && \
    chmod +x /usr/local/bin/ffuf && \
    rm -f /tmp/ffuf.tar.gz || echo "[!] Warning: ffuf install failed, continuing"

# 4. radare2 (lightweight binary/reverse analysis)
apt-get update && apt-get install -y --no-install-recommends radare2 || echo "[!] Warning: radare2 apt install failed"
rm -rf /var/lib/apt/lists/*

# 5. GDB Plugin (gef default for speed & reliability; optional pwndbg)
if [ "${GDB_PLUGIN:-gef}" = "gef" ]; then
    curl -fsSL https://gef.blah.cat/py -o /root/.gdbinit-gef.py && \
        echo "source /root/.gdbinit-gef.py" >> /root/.gdbinit || echo "[!] Warning: GEF download failed"
elif [ "${GDB_PLUGIN:-gef}" = "pwndbg" ]; then
    git clone --depth=1 https://github.com/pwndbg/pwndbg /opt/pwndbg && \
        (cd /opt/pwndbg && ./setup.sh) || echo "[!] Warning: pwndbg install failed"
fi

# 6. Optional SageMath (large image size, disabled by default)
if [ "${INSTALL_SAGE:-false}" = "true" ]; then
    echo "[*] Installing SageMath (INSTALL_SAGE=true)..."
    apt-get update && apt-get install -y --no-install-recommends sagemath && \
        rm -rf /var/lib/apt/lists/*
fi

echo "[*] CTF toolchain setup complete."
