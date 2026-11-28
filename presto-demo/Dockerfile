# Use the official Python slim image
FROM python:3.15-slim

# Set working directory
WORKDIR /app

# Copy application files
COPY app.py .

# Install dependencies (Flask)
RUN pip install --no-cache-dir Flask

# Expose the port Cloud Run expects
EXPOSE 8080

# Command to run the Flask app
CMD ["python", "app.py"]
