"""A tiny MQTT 3.1.1 broker for run_tests.py, in a thread of the test process.

Clients connect with any id (the user name and password are recorded, and refused with CONNACK 4
when `password` is set and does not match), subscribe with + and # wildcards, publish at QoS 0 or 1
(QoS 1 gets its PUBACK), receive retained messages on subscribe, and a client that drops without a
DISCONNECT has its will published. Nothing persists; nothing is queued for a client that is away.

    b = Broker(port); b.wait(lambda: "matchy/status" in b.retained); b.publish("matchy/cmd/next", "PRESS")
"""
import socketserver
import struct
import sys
import threading
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "matchy", "app"))
from mqtt import CONNECT, CONNACK, PUBLISH, PUBACK, SUBSCRIBE, SUBACK, PINGREQ, PINGRESP, DISCONNECT  # noqa: E402
from mqtt import packet, read_packet, parse_publish  # noqa: E402


def matches(filt, topic):
    f, t = filt.split("/"), topic.split("/")
    for i, part in enumerate(f):
        if part == "#":
            return True
        if i >= len(t) or (part != "+" and part != t[i]):
            return False
    return len(f) == len(t)


def _field(data, pos):
    n = struct.unpack(">H", data[pos:pos + 2])[0]
    return data[pos + 2:pos + 2 + n], pos + 2 + n


class Broker:
    def __init__(self, port, host="127.0.0.1", password=None):
        self.password = password     # when set, every client must send it
        self.retained = {}           # topic -> payload (str)
        self.log = []                # every PUBLISH received: (topic, payload, retain)
        self.clients = {}            # handler -> {"id", "username", "password", "subs", "will"}
        self.connects = []           # (client id, username, password) in order
        self.cond = threading.Condition()
        broker = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                broker.serve(self)

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server((host, port), Handler)
        threading.Thread(target=self.server.serve_forever, name="fakebroker", daemon=True).start()

    # ---------------- what the test uses ----------------
    def publish(self, topic, payload, retain=False):
        self.route(topic, payload if isinstance(payload, bytes) else str(payload).encode(), retain)

    def wait(self, pred, timeout=15):
        with self.cond:
            return self.cond.wait_for(pred, timeout)

    def kick(self):
        """Drop every client without a DISCONNECT: their wills are published."""
        with self.cond:
            handlers = list(self.clients)
        for h in handlers:
            try:
                h.request.shutdown(2)
            except OSError:
                pass

    def close(self):
        self.kick()
        self.server.shutdown()
        self.server.server_close()

    # ---------------- the protocol ----------------
    def route(self, topic, payload, retain, sender=None):
        text = payload.decode("utf-8", "replace")
        with self.cond:
            self.log.append((topic, text, retain))
            if retain:
                if payload:
                    self.retained[topic] = text
                else:
                    self.retained.pop(topic, None)
            targets = [(h, c) for h, c in self.clients.items() if any(matches(f, topic) for f in c["subs"])]
            self.cond.notify_all()
        for h, c in targets:
            self.send(h, c, packet(PUBLISH, 0, struct.pack(">H", len(topic.encode())) + topic.encode() + payload))

    def send(self, handler, client, data):
        with client["wlock"]:
            try:
                handler.request.sendall(data)
            except OSError:
                pass

    def serve(self, handler):
        sock, client, clean = handler.request, None, False
        try:
            ptype, _flags, body = read_packet(sock)
            if ptype != CONNECT:
                return
            _proto, pos = _field(body, 0)
            flags, keepalive = body[pos + 1], struct.unpack(">H", body[pos + 2:pos + 4])[0]
            pos += 4
            cid, pos = _field(body, pos)
            will = None
            if flags & 0x04:
                wt, pos = _field(body, pos)
                wm, pos = _field(body, pos)
                will = (wt.decode(), wm, bool(flags & 0x20))
            user = pw = None
            if flags & 0x80:
                user, pos = _field(body, pos)
                user = user.decode()
            if flags & 0x40:
                pw, pos = _field(body, pos)
                pw = pw.decode()
            sock.settimeout(max(keepalive * 1.5, 5) if keepalive else None)
            with self.cond:
                self.connects.append((cid.decode(), user, pw))
                self.cond.notify_all()
            if self.password is not None and pw != self.password:
                sock.sendall(packet(CONNACK, 0, b"\x00\x04"))
                return
            client = {"id": cid.decode(), "username": user, "password": pw, "subs": [], "will": will,
                      "wlock": threading.Lock()}
            with self.cond:
                self.clients[handler] = client
                self.cond.notify_all()
            sock.sendall(packet(CONNACK, 0, b"\x00\x00"))
            while True:
                ptype, flags, body = read_packet(sock)
                if ptype == PUBLISH:
                    topic, payload, qos, pid = parse_publish(flags, body)
                    if qos == 1:
                        self.send(handler, client, packet(PUBACK, 0, struct.pack(">H", pid)))
                    self.route(topic, payload, bool(flags & 1), sender=handler)
                elif ptype == SUBSCRIBE:
                    pid, pos, codes, retained = struct.unpack(">H", body[:2])[0], 2, b"", []
                    while pos < len(body):
                        filt, pos = _field(body, pos)
                        filt, pos = filt.decode(), pos + 1  # + the requested QoS byte
                        client["subs"].append(filt)
                        codes += b"\x00"
                        with self.cond:
                            retained += [(t, p) for t, p in self.retained.items() if matches(filt, t)]
                    self.send(handler, client, packet(SUBACK, 0, struct.pack(">H", pid) + codes))
                    for t, p in retained:
                        self.send(handler, client, packet(PUBLISH, 1, struct.pack(">H", len(t.encode())) + t.encode() + p.encode()))
                elif ptype == PINGREQ:
                    self.send(handler, client, packet(PINGRESP, 0))
                elif ptype == DISCONNECT:
                    clean = True
                    return
        except (OSError, ValueError, struct.error):
            pass
        finally:
            if client:
                with self.cond:
                    self.clients.pop(handler, None)
                    self.cond.notify_all()
                if not clean and client["will"]:
                    self.route(*client["will"])


if __name__ == "__main__":  # a broker to poke a demo engine at by hand
    import time
    Broker(int(sys.argv[1]) if len(sys.argv) > 1 else 1883)
    while True:
        time.sleep(3600)
