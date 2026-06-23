FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directories (overridden by Render's persistent disk at /data)
RUN mkdir -p /data/designs /data/logs

CMD ["python", "main.py"]
