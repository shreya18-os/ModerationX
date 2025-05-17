# Install system deps if needed
RUN apt-get update && apt-get install -y python3-venv python3-pip

# Set up venv and install dependencies directly
RUN python -m venv --copies /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip && \
    /opt/venv/bin/pip install -r requirements.txt
