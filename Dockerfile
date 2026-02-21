FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (cache-friendly)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Create data directories
RUN mkdir -p data/exports logs

# Safety: enforce read-only mode
ENV FORCE_READ_ONLY=true

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import sqlite3; sqlite3.connect('data/trades.db').execute('SELECT 1')" || exit 1

CMD ["python", "main.py", "run"]
