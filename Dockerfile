FROM python:3.13-alpine@sha256:2d9aefe2fef018a7eb2c13064c89c71929800fd2e5dccdbf52ea5da5bb8d929a
WORKDIR /app
COPY app.py index.html ./
USER 65532:65532
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data PORT=8080
EXPOSE 8080
CMD ["python3", "/app/app.py"]
