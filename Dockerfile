FROM mcr.microsoft.com/playwright/python:v1.62.0-noble
WORKDIR /app
COPY . /app
RUN python -m pip install --no-cache-dir .
ENTRYPOINT ["webmcp"]
