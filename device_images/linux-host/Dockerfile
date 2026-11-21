# Minimal lab end-host: a shell with the usual troubleshooting tools that
# stays running (containerlab's linux kind needs a long-lived PID 1).
#   docker build -t plabs/host:latest device_images/linux-host/
FROM alpine:3.20
RUN apk add --no-cache iproute2 iputils bind-tools curl tcpdump iperf3 mtr traceroute openssh-client bash net-snmp-tools iperf
CMD ["sleep", "infinity"]
