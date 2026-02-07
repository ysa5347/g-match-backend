# G-Match Matcher Service
# Runs edge_calculator.py or match_scheduler.py based on SCRIPT_NAME env
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY config.py .
COPY edge_calculator.py .
COPY match_scheduler.py .
COPY email_notifier.py .

# Create non-root user
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

# Default script to run (can be overridden)
# Options: edge_calculator.py, match_scheduler.py
ENV SCRIPT_NAME=edge_calculator.py

CMD ["sh", "-c", "python $SCRIPT_NAME"]
