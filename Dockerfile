# The UFO Evidence Board for a NAS: the dashboard plus a small service that
# follows YouTube channels (saving their captions) and downloads and OCRs
# scanned National Archives files. Works on x86 and ARM.
#   docker compose up -d --build     then open http://<nas-address>:8080

# Stage 1: fetch the two JavaScript libraries so the page needs no CDN at run time.
FROM node:22-alpine AS vendor
WORKDIR /v
RUN npm install --no-audit --no-fund d3@7.9.0 3d-force-graph@1.79.0 \
 && mkdir /out \
 && cp node_modules/d3/dist/d3.min.js node_modules/3d-force-graph/dist/3d-force-graph.min.js /out/

# Stage 2: Python server. yt-dlp is left unpinned on purpose: YouTube changes
# often, and rebuilding the image picks up the latest fixes.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir yt-dlp "youtube-transcript-api>=1.2,<2" "pillow>=10"
WORKDIR /app
COPY server/ /app/server/
COPY ui/ /app/site/
COPY --from=vendor /out/ /app/site/vendor/
RUN python3 /app/server/prepare_site.py /app/site
ENV DATA_DIR=/data SITE_DIR=/app/site PORT=8080 PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8080
CMD ["python3", "/app/server/app.py"]
