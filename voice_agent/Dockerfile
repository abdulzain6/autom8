FROM python:3.11

WORKDIR /app

# Ensures that logs are captured in realtime
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy your application code so the script is available to run
COPY . .

# Run the agent's built-in download command to cache all necessary models
# This is the key change
RUN python agent.py download-files

# The command to start the agent remains the same
CMD ["python", "agent.py", "start"]