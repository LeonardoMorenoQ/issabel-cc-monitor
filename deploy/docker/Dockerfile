# Optional container build (the primary deployment is bare systemd on Issabel 5).
# Build from the repository root:
#   docker build -f deploy/docker/Dockerfile -t ccmon .

# --- dashboard ---------------------------------------------------------------
FROM node:22-slim AS dashboard
WORKDIR /build
COPY dashboard/package.json dashboard/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY dashboard/ ./
RUN npm run build

# --- runtime -----------------------------------------------------------------
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /opt/ccmon

COPY requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock

COPY ccmon/ ./ccmon/
COPY .env.example ./
COPY --from=dashboard /build/dist ./dashboard/dist

RUN useradd --system --home /nonexistent --shell /usr/sbin/nologin ccmon
USER ccmon

EXPOSE 8080
# Configure everything (DATABASE_URL, ISSABEL_DB_*, AMI_* ...) via environment.
CMD ["ccmon", "serve"]
