# Sentinel AI service image (telemetry ingest + detection; later phases add services).
# Runtime dependencies are installed from the lockfile with hash checking.
FROM python:3.12-slim AS build
RUN pip install --no-cache-dir uv==0.13.6
WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY src ./src
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt -o /requirements.txt \
 && uv build --wheel --out-dir /dist

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=build /requirements.txt /tmp/requirements.txt
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
 && pip install --no-cache-dir --no-deps /tmp/*.whl \
 && rm -f /tmp/*.whl /tmp/requirements.txt \
 && useradd --uid 10002 --create-home sentinel
USER 10002
ENTRYPOINT ["sentinelai"]
CMD ["--help"]
