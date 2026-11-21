# Phanero Labs traffic generator: a lab host whose main process is
# plabs-traffic (UDP streams with per-stream DSCP, multicast / SSM receivers,
# a reflector, live stats). Configured through /etc/plabs-traffic/config.json
# (bind-mounted from the lab directory by the app).
#   docker build -t plabs/traffic:latest device_images/traffic/
FROM plabs/host:latest
RUN apk add --no-cache python3
COPY plabs_traffic.py /usr/local/bin/plabs-traffic
RUN chmod +x /usr/local/bin/plabs-traffic && mkdir -p /etc/plabs-traffic
CMD ["/usr/local/bin/plabs-traffic", "daemon"]
