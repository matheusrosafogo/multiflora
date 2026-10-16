FROM python:3.12-bookworm

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG USERNAME=multiflora
ARG USER_UID=1000
ARG USER_GID=1000 

RUN groupadd --gid $USER_GID $USERNAME \
    && useradd --uid $USER_UID --gid $USER_GID -m -s /bin/bash $USERNAME \
    && apt-get update \
    && apt-get install -y sudo \
    && echo "$USERNAME ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/$USERNAME \
    && chmod 0440 /etc/sudoers.d/$USERNAME

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        build-essential \
        ca-certificates \
        cmake \
        curl \
        gawk \
        iputils-ping \
        openssh-client \
        patchelf \
        ccache \
        clang \
        clang-format \
        clangd \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p -m 755 /etc/apt/keyrings \
    && curl -LSfs -o /etc/apt/keyrings/github-cli-archive-keyring.gpg \
        https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/github-cli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/github-cli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && . /etc/os-release \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        docker-ce \
        docker-ce-cli \
        containerd.io \
        docker-buildx-plugin \
        docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*


USER $USERNAME
WORKDIR /home/$USERNAME/app

RUN curl -fsSL https://opencode.ai/v2/install | bash 
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

ENV PYTHONUNBUFFERED=1
ENV DISPLAY=:0
ENV PATH="/home/${USERNAME}/.local/bin:${PATH}"