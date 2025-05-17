# Use a minimal Python base image
FROM python:3.10-slim

# Install system dependencies
RUN apt-get update && apt-get install -y python3-venv python3-pip && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirement file and install packages
COPY requirements.txt .

# Set up virtual environment and install dependencies
RUN python -m venv --copies /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip && \
    /opt/venv/bin/pip install -r requirements.txt

# Copy application code
COPY . .

# Set default path to the venv
ENV PATH="/opt/venv/bin:$PATH"

# Run the bot (adjust this if the entry file is not main.py)
CMD ["python", "main.py"]
