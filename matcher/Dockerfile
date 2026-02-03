FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 실행할 스크립트는 환경변수로 지정
# ENTRYPOINT: edge_calculator.py 또는 match_scheduler.py
ENV SCRIPT_NAME=edge_calculator.py

CMD ["sh", "-c", "python $SCRIPT_NAME"]
