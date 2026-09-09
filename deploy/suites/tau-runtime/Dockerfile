FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt
COPY tau2 /opt/tau2
RUN pip install --no-deps /opt/tau2
COPY worker.py /worker.py
ENV TAU2_DATA_DIR=/opt/tau2/data PYTHONDONTWRITEBYTECODE=1 HOME=/tmp
ENTRYPOINT ["python", "/worker.py"]
