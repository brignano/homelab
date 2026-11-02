# HEIC -> JPEG preview converter (scripts/photos-previews.sh). Pillow's wheels
# bundle libheif through pillow-heif, so no apt layer is needed.
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
RUN pip install --no-cache-dir pillow==13.0.0 pillow-heif==1.8.1
COPY convert.py /convert.py
ENTRYPOINT ["nice", "-n", "19", "python", "-u", "/convert.py"]
