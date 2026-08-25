FROM python:3.13-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# Every top-level module, not a hand-listed set. b_server.py was named alone
# here until it grew imports (providers.py, cards.py), at which point the image
# still built fine and then died at container start on ModuleNotFoundError --
# a build that passes and a program that cannot import is the worst shape a
# failure can take. A glob has nothing to keep in sync.
COPY *.py ./

ENV PATH="/app/.venv/bin:${PATH}"
CMD ["uv", "run", "python", "b_server.py"]
