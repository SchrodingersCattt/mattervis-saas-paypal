FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY mattervis_saas ./mattervis_saas
COPY contest ./contest
RUN pip install --no-cache-dir uv==0.10.0 && uv sync --locked --no-dev --no-editable --python 3.11
ENV HOST=0.0.0.0 PORT=50001 PATH="/app/.venv/bin:$PATH"
EXPOSE 50001
CMD ["python", "-m", "contest.app"]
