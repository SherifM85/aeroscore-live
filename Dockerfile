FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir flask numpy PyJWT opencv-python-headless

COPY . .

RUN mkdir -p db static/uploads

EXPOSE 8080

CMD ["python", "app.py"]
