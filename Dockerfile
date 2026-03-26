FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System dependencies for Odoo Python packages (ldap/lxml/psycopg2/Pillow/reporting).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    gcc \
    g++ \
    git \
    libffi-dev \
    libjpeg62-turbo-dev \
    libldap2-dev \
    libpq-dev \
    libsasl2-dev \
    libssl-dev \
    libxml2-dev \
    libxslt1-dev \
    libyaml-dev \
    tzdata \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first for better Docker layer caching.
COPY odoo/requirements.txt /app/odoo-requirements.txt
COPY scripts/requirements.txt /app/custom-requirements.txt
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r /app/odoo-requirements.txt -r /app/custom-requirements.txt

# Copy app source.
COPY odoo /app/odoo
COPY custom_addons /app/custom_addons
COPY scripts /app/scripts

RUN chmod +x /app/scripts/railway_start.sh

# Railway injects PORT; default for local docker runs.
ENV PORT=8069 \
    ODOO_DATA_DIR=/data \
    ODOO_ADDONS_PATH=/app/odoo/addons,/app/custom_addons \
    ODOO_PROXY_MODE=True \
    ODOO_LIST_DB=False \
    ODOO_WORKERS=0 \
    ODOO_MAX_CRON_THREADS=1

EXPOSE 8069

CMD ["/app/scripts/railway_start.sh"]
