FROM python:3.11

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Create a non-root user
RUN useradd -m appuser

COPY . .

# Change ownership of the app folder
RUN chown -R appuser:appuser /app

# Switch user
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]