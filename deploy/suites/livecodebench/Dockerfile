FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
RUN pip install --no-cache-dir numpy==2.2.6
WORKDIR /grader
ADD --checksum=sha256:b7cb6a8a69807bb868150a61742e25d7bb5328bbe01d514471b6ec43c9fa9ed2 https://raw.githubusercontent.com/LiveCodeBench/LiveCodeBench/28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24/lcb_runner/evaluation/testing_util.py /grader/testing_util.py
RUN chmod 444 /grader/testing_util.py
COPY worker.py /grader/worker.py
USER 65534:65534
ENV PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["python", "/grader/worker.py"]
