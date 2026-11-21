# Serves the dashboard (ui/) as a static site. Multi-arch: works on x86 and ARM NAS boxes.
#   docker compose up -d --build     then open http://<nas-address>:8080

# Stage 1: fetch the two JavaScript libraries so the page needs no CDN at run time.
FROM node:22-alpine AS vendor
WORKDIR /v
RUN npm install --no-audit --no-fund d3@7.9.0 3d-force-graph@1.79.0 \
 && mkdir /out \
 && cp node_modules/d3/dist/d3.min.js node_modules/3d-force-graph/dist/3d-force-graph.min.js /out/

# Stage 2: wrap the page in a full HTML document and point it at the local libraries.
FROM nginx:1.27-alpine
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY ui/ /usr/share/nginx/html/
COPY --from=vendor /out/ /usr/share/nginx/html/vendor/
RUN cd /usr/share/nginx/html \
 && { printf '<!doctype html>\n<html lang="en">\n<head>\n<meta name="viewport" content="width=device-width, initial-scale=1">\n</head>\n<body>\n'; \
      sed -e 's#https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js#vendor/d3.min.js#' \
          -e 's#https://cdn.jsdelivr.net/npm/3d-force-graph@1.79.0/dist/3d-force-graph.min.js#vendor/3d-force-graph.min.js#' index.html; \
      printf '\n</body>\n</html>\n'; } > index.tmp \
 && mv index.tmp index.html \
 && grep -q 'vendor/d3.min.js' index.html && grep -q 'vendor/3d-force-graph.min.js' index.html
EXPOSE 80
