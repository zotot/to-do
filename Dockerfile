FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app
# Lecture des relevés PDF
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY assistant_financier/ assistant_financier/
COPY exemples/ exemples/

# Vos relevés et votre budget.json sont montés ici (jamais copiés dans l'image).
WORKDIR /donnees
RUN useradd --create-home --uid 1000 assistant && chown assistant /donnees
USER assistant

ENTRYPOINT ["python", "-m", "assistant_financier"]
