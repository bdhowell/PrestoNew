# Use the official Python slim image
FROM python:3.14-slim

# Set working directory
WORKDIR /app

# Install system dependencies (if any) - none needed for now

# Copy requirements and install them first for caching
COPY requirements.txt .
RUN pip install --no-cache-dir --root-user-action -r requirements.txt

# Copy the rest of the application code
COPY . .

# Expose the port Cloud Run expects
EXPOSE 8080

# Command to run the FastAPI app with Uvicorn
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
