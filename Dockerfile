#
# Build trade-executor as a Docker container
#
# See https://stackoverflow.com/a/71786211/315168 for the recipe
#
FROM python:3.10.8

ENV PYTHONDONTWRITEBYTECODE 1 \
    PYTHONUNBUFFERED 1

RUN apt-get update \
    && apt-get install curl -y \
    && curl -sSL https://install.python-poetry.org | python - --version 1.2.2

ENV PATH="/root/.local/bin:$PATH"

WORKDIR /usr/src/trade-executor

# package source code
COPY . .

RUN poetry config virtualenvs.create false
RUN poetry install --no-dev --no-interaction --no-ansi -E web-server -E execution

# Webhook port 3456
EXPOSE 3456

# Use --quiet to supress Skipping virtualenv creation, as specified in config file.
# https://stackoverflow.com/questions/74564601/poetry-echos-skipping-virtualenv-creation-as-specified-in-config-file-when-r
CMD ["poetry", "run", "--quiet", "trade-executor"]

ENTRYPOINT ["scripts/docker-entrypoint.sh"]