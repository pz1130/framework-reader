# Optional image. The supported local path is still `fr serve` on the host.
# HTTPS is not terminated here — put Caddy (or another proxy) in front.
# See docker-compose.yml and deploy/compose.https.yml.

FROM python:3.12-slim AS builder
WORKDIR /src
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml constraints.txt README.md ./
COPY src ./src
RUN pip install --no-cache-dir --root-user-action=ignore -c constraints.txt . \
    && python -c "from pathlib import Path; import framework_reader.identity as i, framework_reader.prompts as p, framework_reader.web as w; \
assert (Path(i.__file__).parent/'schema.sql').is_file(); \
assert (Path(p.__file__).parent/'drafter.md').is_file(); \
assert (Path(w.__file__).parent/'static'/'favicon.svg').is_file()"
COPY content ./content
COPY scripts ./scripts
# NIST public-domain sources; vendor/ is gitignored on the host.
RUN chmod +x scripts/fetch_sources.sh \
    && ./scripts/fetch_sources.sh \
    && python -m framework_reader.pack.build

FROM python:3.12-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --home-dir /data --no-create-home app \
    && mkdir -p /data /app /opt/framework-reader \
    && chown app:app /data
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/fr /usr/local/bin/fr
COPY --from=builder /src/content /app/content
COPY --from=builder /src/build/content.sqlite /opt/framework-reader/content.sqlite
COPY deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh \
    && chown -R app:app /app /opt/framework-reader
# Starts as root so the named volume can be chowned, then drops to `app`.
ENV FR_DATA_DIR=/data \
    FR_CONTENT_DB=/data/content.sqlite \
    FRAMEWORK_READER_HOME=/data/home \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/', timeout=4)"
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["fr", "serve", "--host", "0.0.0.0", "--port", "8765", "--db", "/data/content.sqlite"]
