FROM python:3.12-slim
WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ .
EXPOSE 8674
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8674"]
