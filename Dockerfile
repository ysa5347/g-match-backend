# Stage 1: Build stage
FROM python:3.13.9-slim AS builder

WORKDIR /build

# Install system dependencies for building
RUN apt-get update && apt-get install -y \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies to a clean prefix
RUN pip install --no-cache-dir --prefix=/build/deps -r requirements.txt

# Stage 2: Runtime stage
FROM python:3.13.9-slim

WORKDIR /app

# Install runtime dependencies only
RUN apt-get update && apt-get install -y \
    default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 appuser

# Copy Python dependencies from builder into system site-packages
COPY --from=builder /build/deps /usr/local

# Copy application code (tests/, matcher/ excluded by .dockerignore)
COPY g_match/ ./g_match/
COPY account/ ./account/
COPY match/ ./match/
COPY manage.py .

# Set ownership
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1alpha1/account/')" || exit 1

# Run gunicorn
# CMD ["gunicorn", "g_match.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
