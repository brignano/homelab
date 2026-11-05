# HEIC -> JPEG preview converter (scripts/photos-previews.sh). Pillow's wheels
# bundle libheif through pillow-heif, so no apt layer is needed.
FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54
RUN pip install --no-cache-dir pillow==13.0.0 pillow-heif==1.8.1
COPY convert.py /convert.py
ENTRYPOINT ["nice", "-n", "19", "python", "-u", "/convert.py"]
