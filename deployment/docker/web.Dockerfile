# INM web edge (role 01 presentation): builds the React app, serves it over HTTPS and reverse-proxies /api and /ws.
ARG NODE_IMAGE=node:22-alpine
ARG NGINX_IMAGE=nginx:1.28-alpine
FROM ${NODE_IMAGE} AS build
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM ${NGINX_IMAGE}
RUN rm -f /etc/nginx/conf.d/default.conf
COPY configs/nginx/inm.conf /etc/nginx/conf.d/inm.conf
COPY --from=build /src/dist /usr/share/nginx/html
# Static files must be readable by unprivileged Nginx workers even if the
# root-run Git checkout was created with umask 077.
RUN chmod -R a+rX /usr/share/nginx/html && chmod a+r /etc/nginx/conf.d/inm.conf
EXPOSE 8443
HEALTHCHECK --interval=15s --timeout=5s --retries=8 CMD wget -q --no-check-certificate -O /dev/null https://127.0.0.1:8443/healthz || exit 1
