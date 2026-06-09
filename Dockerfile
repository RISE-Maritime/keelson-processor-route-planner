# syntax=docker/dockerfile:1
FROM python:3.13-slim-bookworm

# uv for fast, no-apt dependency install. Zombie reaping + signal forwarding is handled by
# Docker's built-in init (compose `init: true`), so we don't install tini — keeping the build
# apt-free and portable.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# 1) Third-party dependencies, in a layer keyed ONLY on pyproject.toml. Editing application
#    code does not re-resolve or re-download these. The uv cache mount persists wheels across
#    builds, so the big geospatial wheels (shapely/pyproj) download once.
COPY pyproject.toml README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system -r pyproject.toml

# 2) Install just this package (deps already satisfied). This is the only layer that reruns on
#    a code change, and it does no network I/O.
COPY src src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --no-deps .

COPY --chmod=555 ./bin/* /usr/local/bin/

# Run as non-root (principle of least privilege)
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER app

ENTRYPOINT ["route_planner"]
CMD ["--connect", "tcp/host.docker.internal:7447"]
