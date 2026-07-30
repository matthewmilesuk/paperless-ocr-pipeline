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
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
