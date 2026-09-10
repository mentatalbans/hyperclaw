FROM python:3.13.15-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY examples/mcp-docs/requirements.lock /opt/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r /opt/requirements.lock
COPY src/hyperclaw /usr/local/lib/python3.13/site-packages/hyperclaw
COPY examples/mcp-docs/server.py /opt/server.py
USER 65532:65532
WORKDIR /docs
ENTRYPOINT ["/usr/local/bin/python"]
CMD ["/opt/server.py"]
