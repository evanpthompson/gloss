FROM python:3.13-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY b_server.py ./

ENV PATH="/app/.venv/bin:${PATH}"
CMD ["uv", "run", "python", "b_server.py"]
