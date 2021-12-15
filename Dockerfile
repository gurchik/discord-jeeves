FROM python:3.10-alpine

# needed for PyNaCl
RUN apk add --no-cache --virtual .pynacl_build_deps build-base python3-dev libffi-dev

# needed for streaming opus sound
RUN apk add libopusenc
# or is it libopus?

# needed for youtube-dl
RUN apk add ffmpeg

ADD . /app/

WORKDIR /app
RUN pip install -r requirements.txt

RUN apk del .pynacl_build_deps

ENTRYPOINT "/app/start.sh"