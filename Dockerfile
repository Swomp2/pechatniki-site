FROM docker.io/library/python:3.12.13-slim-trixie AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_NO_CACHE=1
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.27 /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM docker.io/library/python:3.12.13-slim-trixie AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DJANGO_SQLITE_PATH=/app/data/db.sqlite3
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app/data /app/media /app/staticfiles /tmp \
    && chown -R app:app /app /tmp

COPY --from=builder --chown=app:app /opt/venv /opt/venv
COPY --chown=app:app manage.py entrypoint.sh ./
COPY --chown=app:app config ./config
COPY --chown=app:app problems ./problems
COPY --chown=app:app static ./static
COPY --chown=app:app templates ./templates
RUN chmod 0755 /app/entrypoint.sh

USER app:app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import os, urllib.request; host=(os.environ.get('DJANGO_HEALTHCHECK_HOST') or os.environ.get('DJANGO_ALLOWED_HOSTS','localhost').split(',')[0].strip() or 'localhost'); req=urllib.request.Request('http://127.0.0.1:8000/health/', headers={'Host': host, 'X-Forwarded-Proto': 'https'}); urllib.request.urlopen(req, timeout=3).read()" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--worker-class", "gthread", "--workers", "2", "--threads", "2", "--timeout", "60", "--graceful-timeout", "30", "--keep-alive", "5", "--max-requests", "1000", "--max-requests-jitter", "100", "--worker-tmp-dir", "/tmp", "--error-logfile", "-"]
