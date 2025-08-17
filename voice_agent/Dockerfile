FROM python:3.11

WORKDIR /app

# Ensures that logs are captured in realtime
ENV PYTHONUNBUFFERED=1

# 1. Copy only the dependency file first.
# This layer only changes if you modify requirements.txt.
COPY requirements.txt .

# 2. Install dependencies.
# This step will use the cache as long as requirements.txt hasn't changed.
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py .

RUN python agent.py download-files
# 3. Copy the rest of your application code.
# This layer now only changes if your source files change.
COPY . .

# 4. Run the download command.
# This now ONLY runs if your source code (step 3) or your
# dependencies (step 1) have changed.

# 5. Set the final command to run the application.
CMD ["python", "agent.py", "start"]