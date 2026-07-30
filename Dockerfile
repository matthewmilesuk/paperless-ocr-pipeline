FROM python:3.12-slim

# ocrmypdf/pdf2image runtime deps: poppler for pdf2image, tesseract as an
# ocrmypdf fallback OCR engine (Document AI is the primary OCR path) and
# for pipeline/orient.py's rotate-pages orientation detection,
# ghostscript/qpdf which ocrmypdf shells out to, and pngquant which
# ocrmypdf's --optimize {2,3} (pipeline/pdfa.py) requires -- confirmed as
# a hard failure without it, not a soft warning.
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    ghostscript \
    qpdf \
    pngquant \
    default-jre-headless \
    wget \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# veraPDF (PDF/A validation, pipeline/validate.py). Not apt-installable --
# it's an IzPack installer, run here non-interactively via an automated-
# install XML config (docker/verapdf-auto-install.xml). default-jre-headless
# above provides the JRE it needs. Version pinned via the versioned download
# URL, not "latest" -- see AGENTS.md "Local development" for how to bump it
# deliberately (there's no lockfile mechanism for this the way pip-tools
# handles Python deps).
COPY docker/verapdf-auto-install.xml /tmp/verapdf-auto-install.xml
RUN wget -q -O /tmp/verapdf-installer.zip \
        https://software.verapdf.org/releases/1.30/verapdf-greenfield-1.30.2-installer.zip \
    && unzip -q /tmp/verapdf-installer.zip -d /tmp/verapdf-installer \
    && cd /tmp/verapdf-installer/verapdf-greenfield-1.30.2 \
    && chmod +x verapdf-install \
    && ./verapdf-install /tmp/verapdf-auto-install.xml \
    && ln -s /opt/verapdf/verapdf /usr/local/bin/verapdf \
    && rm -rf /tmp/verapdf-installer.zip /tmp/verapdf-installer /tmp/verapdf-auto-install.xml

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
