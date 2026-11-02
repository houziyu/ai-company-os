# syntax=docker/dockerfile:1.7
# Frontend image: build the SPA, then serve it with nginx which also proxies
# /api to the api service. This is the only container published to the host.

FROM node:22-alpine AS build

WORKDIR /app

# Corepack provides the pinned pnpm version from package.json.
RUN corepack enable && corepack prepare pnpm@10.34.9 --activate

# Only the files needed for the build are copied, so the docs/ tree and the
# backend are not part of the frontend build context.
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
COPY docs/design/design-tokens.json /docs/design/design-tokens.json

# The generator resolves ../../docs relative to frontend/scripts; in the image
# the frontend lives at /app, so point it at the copied token file.
ENV DESIGN_TOKENS_PATH=/docs/design/design-tokens.json
RUN pnpm build

FROM nginx:1.29-alpine AS runtime

RUN apk add --no-cache curl

# Replace the default main config: the stock one writes its pid and temp files
# to paths the unprivileged user cannot always write inside the container.
COPY deploy/nginx/nginx.conf /etc/nginx/nginx.conf
COPY deploy/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY deploy/nginx/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /app/dist /usr/share/nginx/html

RUN chown -R nginx:nginx /usr/share/nginx/html \
    && mkdir -p /tmp/client_body /tmp/proxy /tmp/fastcgi /tmp/uwsgi /tmp/scgi \
    && chown -R nginx:nginx /tmp

USER nginx

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
