FROM node:24-bookworm-slim@sha256:ba849c60be29959425b8734d57b8b4b7d56f98edd9504c9af091d5281095a71e
WORKDIR /opt/agent
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
ENV PATH="/opt/agent/node_modules/.bin:${PATH}" \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    DISABLE_AUTOUPDATER=1
WORKDIR /workspace
USER node
ENTRYPOINT ["claude-agent-acp"]
