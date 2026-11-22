# syntax=docker/dockerfile:1.6
FROM --platform=linux/amd64 ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
ARG INSTALL_SAGE=false
ARG GDB_PLUGIN=gef

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TERM=xterm-256color \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WORKSPACE=/workspace

# 1. Enable i386 multiarch & install core system + CTF binary/network/misc tools
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        gnupg \
        software-properties-common \
        tzdata \
        # Terminal & shell utilities
        bash \
        tmux \
        coreutils \
        findutils \
        grep \
        sed \
        gawk \
        jq \
        ripgrep \
        file \
        xxd \
        vim \
        nano \
        git \
        curl \
        wget \
        unzip \
        p7zip-full \
        tar \
        gzip \
        bzip2 \
        xz-utils \
        # Build & 32-bit runtime libraries (Pwn / Reverse)
        build-essential \
        gcc-multilib \
        g++-multilib \
        libc6:i386 \
        libncurses5:i386 \
        libstdc++6:i386 \
        lib32z1 \
        libc6-dbg \
        libc6-dbg:i386 \
        gdb \
        gdb-multiarch \
        strace \
        ltrace \
        binutils \
        elfutils \
        patchelf \
        nasm \
        qemu-user \
        # Python & Ruby runtime
        python3 \
        python3-pip \
        python3-dev \
        python3-venv \
        ruby \
        ruby-dev \
        libffi-dev \
        libssl-dev \
        libgmp-dev \
        libmpfr-dev \
        libmpc-dev \
        # Network & Web recon
        netcat-openbsd \
        socat \
        nmap \
        dnsutils \
        iputils-ping \
        iproute2 \
        openssh-client \
        sqlmap \
        # Misc / Forensics / Stego
        binwalk \
        exiftool \
        steghide \
        foremost \
        fcrackzip \
        john \
        zbar-tools \
        tesseract-ocr \
        tshark \
    && rm -rf /var/lib/apt/lists/*

# 2. Install Python CTF libraries (Pwn / Crypto / Web / Reverse / Misc)
RUN python3 -m pip install --upgrade pip setuptools wheel && \
    python3 -m pip install \
        pwntools \
        ROPgadget \
        pycryptodome \
        sympy \
        z3-solver \
        gmpy2 \
        requests \
        httpx \
        beautifulsoup4 \
        lxml \
        pillow \
        pypdf \
        scapy \
        capstone \
        unicorn \
        keystone-engine \
        r2pipe \
        flask-unsign

# 3. Install additional specialized CTF tools (checksec, one_gadget, ffuf, radare2, GDB plugin)
COPY tools_install.sh /tmp/tools_install.sh
RUN sed -i 's/\r$//' /tmp/tools_install.sh && \
    chmod +x /tmp/tools_install.sh && \
    INSTALL_SAGE="${INSTALL_SAGE}" GDB_PLUGIN="${GDB_PLUGIN}" /tmp/tools_install.sh && \
    rm -f /tmp/tools_install.sh

# 4. Prepare workspace, read-only skills mountpoint & log directories
RUN mkdir -p /workspace/challenge /opt/agan/skills /tmp/agan_logs /tmp/agan_cmds && \
    chmod 777 /tmp/agan_logs /tmp/agan_cmds

COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

WORKDIR /workspace

ENTRYPOINT ["/entrypoint.sh"]
