FROM alpine:3.22
RUN apk add --no-cache iproute2 iptables
COPY entrypoint.sh /usr/local/bin/lab-node
RUN chmod 755 /usr/local/bin/lab-node
ENTRYPOINT ["/usr/local/bin/lab-node"]
