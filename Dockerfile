#
# Spiderfoot Dockerfile
#
# http://www.spiderfoot.net
#
# Written by: Michael Pellon <m@pellon.io>
# Updated by: Chandrapal <bnchandrapal@protonmail.com>
# Updated by: Steve Micallef <steve@binarypool.com>
# Updated by: Steve Bate <svc-spiderfoot@stevebate.net>
#    -> Inspired by https://github.com/combro2k/dockerfiles/tree/master/alpine-spiderfoot
#
# On this branch the default command runs bot.py (Telegram bot
# front-end), not the bare web UI, so it can be built as-is on a
# platform that just wants a Dockerfile (e.g. BotHost's Docker mode).
#
# Usage:
#
#   sudo docker build -t spiderfoot-bot .
#   sudo docker run -e TELEGRAM_BOT_TOKEN=... -e ALLOWED_USER_IDS=... spiderfoot-bot
#
# Using Docker volume for spiderfoot data
#
#   sudo docker run -v /mydir/spiderfoot:/var/lib/spiderfoot spiderfoot-bot
#
# Running the bare web UI instead of the bot (override the default command)
#
#   sudo docker run -p 5001:5001 spiderfoot-bot sf.py -l 0.0.0.0:5001
#
# Using SpiderFoot remote command line with web server
#
#   docker run --rm -it spiderfoot-bot sfcli.py -s http://my.spiderfoot.host:5001/
#
# Running spiderfoot commands without web server (can optionally specify volume)
#
#   sudo docker run --rm spiderfoot-bot sf.py -h
#
# Running a shell in the container for maintenance
#   sudo docker run -it --entrypoint /bin/sh spiderfoot-bot
#
# Running spiderfoot unit tests in container
#
#   sudo docker build -t spiderfoot-test --build-arg REQUIREMENTS=test/requirements.txt .
#   sudo docker run --rm spiderfoot-test -m pytest --flake8 .

FROM alpine:3.12.4 AS build
ARG REQUIREMENTS=requirements.txt
RUN apk add --no-cache gcc git curl python3 python3-dev py3-pip swig tinyxml-dev \
 python3-dev musl-dev openssl-dev libffi-dev libxslt-dev libxml2-dev jpeg-dev \
 openjpeg-dev zlib-dev cargo rust
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin":$PATH
COPY $REQUIREMENTS requirements.txt ./
RUN ls
RUN echo "$REQUIREMENTS"
RUN pip3 install -U pip
RUN pip3 install -r "$REQUIREMENTS"



FROM alpine:3.13.0
WORKDIR /home/spiderfoot

# Place database and logs outside installation directory
ENV SPIDERFOOT_DATA /var/lib/spiderfoot
ENV SPIDERFOOT_LOGS /var/lib/spiderfoot/log
ENV SPIDERFOOT_CACHE /var/lib/spiderfoot/cache

# Run everything as one command so that only one layer is created
RUN apk --update --no-cache add python3 musl openssl libxslt tinyxml libxml2 jpeg zlib openjpeg \
    && addgroup spiderfoot \
    && adduser -G spiderfoot -h /home/spiderfoot -s /sbin/nologin \
               -g "SpiderFoot User" -D spiderfoot \
    && rm -rf /var/cache/apk/* \
    && rm -rf /lib/apk/db \
    && rm -rf /root/.cache \
    && mkdir -p $SPIDERFOOT_DATA || true \
    && mkdir -p $SPIDERFOOT_LOGS || true \
    && mkdir -p $SPIDERFOOT_CACHE || true \
    && chown spiderfoot:spiderfoot $SPIDERFOOT_DATA \
    && chown spiderfoot:spiderfoot $SPIDERFOOT_LOGS \
    && chown spiderfoot:spiderfoot $SPIDERFOOT_CACHE

COPY . .
COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

USER spiderfoot

# Run the application. bot.py starts the SpiderFoot web UI itself,
# bound to 127.0.0.1 only, and talks to Telegram over outbound
# long-polling -- no inbound port needs to be exposed.
ENTRYPOINT ["/opt/venv/bin/python"]
CMD ["bot.py"]
