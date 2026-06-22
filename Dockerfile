FROM python:3.11-slim

# System deps needed by OpenCV headless and mediapipe
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    libgl1 \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy project files
COPY requirements.txt .

# Install headless OpenCV first, then mediapipe without its opencv dependency
RUN pip install --no-cache-dir flask numpy PyJWT opencv-python-headless \
    && pip install --no-cache-dir mediapipe --no-deps \
    && pip install --no-cache-dir absl-py flatbuffers sounddevice matplotlib attrs protobuf

COPY . .

# Create required directories
RUN mkdir -p db static/uploads

EXPOSE 8080

CMD ["python", "app.py"]
