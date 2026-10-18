FROM ubuntu:25.10

ENV DEBIAN_FRONTEND=noninteractive \
    container=docker

RUN apt-get update \
 && apt-get install -y --no-install-recommends systemd systemd-sysv dbus python3 sudo ca-certificates curl gnupg iproute2 procps less \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/* \
 && rm -f /lib/systemd/system/multi-user.target.wants/* \
          /etc/systemd/system/*.wants/* \
          /lib/systemd/system/local-fs.target.wants/* \
          /lib/systemd/system/sockets.target.wants/*udev* \
          /lib/systemd/system/sockets.target.wants/*initctl* \
          /lib/systemd/system/systemd-update-utmp* \
 && systemctl mask systemd-logind.service getty.target console-getty.service

STOPSIGNAL SIGRTMIN+3

CMD ["/lib/systemd/systemd"]
