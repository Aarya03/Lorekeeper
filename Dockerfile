FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
COPY lorekeeper ./lorekeeper
COPY web ./web
RUN mkdir -p /app/data
EXPOSE 8787
CMD ["python", "server.py"]
