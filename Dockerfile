FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

ARG ODOO_VERSION=18.0
ARG TARGETARCH

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
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-ron \
    tzdata \
    zlib1g-dev \
    && if [ -z "${TARGETARCH}" ]; then TARGETARCH="$(dpkg --print-architecture)"; fi \
    && WKHTMLTOPDF_ARCH="${TARGETARCH}" \
    && case "${TARGETARCH}" in \
        "amd64") WKHTMLTOPDF_SHA="e9f95436298c77cc9406bd4bbd242f4771d0a4b2" ;; \
        "arm64") WKHTMLTOPDF_SHA="77bc06be5e543510140e6728e11b7c22504080d4" ;; \
        "ppc64le" | "ppc64el") WKHTMLTOPDF_ARCH="ppc64el"; WKHTMLTOPDF_SHA="d61c2497fa6edb4650548b8864f53c7de161347d" ;; \
        *) echo "Unsupported architecture for wkhtmltox: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && curl -o /tmp/wkhtmltox.deb -sSL "https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-3/wkhtmltox_0.12.6.1-3.bookworm_${WKHTMLTOPDF_ARCH}.deb" \
    && echo "${WKHTMLTOPDF_SHA} /tmp/wkhtmltox.deb" | sha1sum -c - \
    && apt-get install -y --no-install-recommends /tmp/wkhtmltox.deb \
    && rm -f /tmp/wkhtmltox.deb \
    && rm -rf /var/lib/apt/lists/*

# Fetch Odoo source during build so deployment does not depend on Git submodule checkout.
RUN git clone --depth 1 --branch ${ODOO_VERSION} https://github.com/odoo/odoo.git /app/odoo

# Install Python dependencies first for better Docker layer caching.
COPY scripts/requirements.txt /app/custom-requirements.txt
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r /app/odoo/requirements.txt -r /app/custom-requirements.txt

# Copy app source.
COPY custom_addons /app/custom_addons
COPY scripts /app/scripts

RUN chmod +x /app/scripts/railway_start.sh /app/scripts/railway_migrate.sh

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
