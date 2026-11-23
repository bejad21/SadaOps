FROM python:3.11-slim

# Non-root sandbox user for isolated Python computation (Phase 2)
RUN useradd --create-home --shell /bin/bash sandbox
USER sandbox
WORKDIR /home/sandbox

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

CMD ["python3", "-c", "print('SadaOps sandbox ready')"]
