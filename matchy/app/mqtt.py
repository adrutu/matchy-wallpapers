"""A small MQTT 3.1.1 client on the standard library, for Home Assistant discovery over a broker on
the local network. It connects with a user name, a password and a last will, publishes at QoS 0,
subscribes, answers the broker's keep-alive with pings, and reconnects with a backoff.

Not here on purpose: TLS, QoS 2, persistent sessions, MQTT 5. If any of those is ever needed, a real
client library is the right move (see the note in DOCS.md).
"""
import socket
import struct
import threading
import time

CONNECT, CONNACK, PUBLISH, PUBACK, SUBSCRIBE, SUBACK, PINGREQ, PINGRESP, DISCONNECT = 1, 2, 3, 4, 8, 9, 12, 13, 14
CONNACK_REASONS = {1: "unacceptable protocol version", 2: "client id rejected", 3: "broker unavailable",
                   4: "bad user name or password", 5: "not authorised"}
FIRST_RETRY_S, MAX_RETRY_S = 5, 300


class MqttError(Exception):
    """The broker refused us or spoke something that is not MQTT 3.1.1."""


def _bytes(s):
    return s if isinstance(s, (bytes, bytearray)) else str(s).encode("utf-8")


def _str(s):
    b = _bytes(s)
    return struct.pack(">H", len(b)) + b


def _remaining(n):
    out = bytearray()
    while True:
        d, n = n % 128, n // 128
        out.append(d | 0x80 if n else d)
        if not n:
            return bytes(out)


def packet(ptype, flags, body=b""):
    return bytes([(ptype << 4) | flags]) + _remaining(len(body)) + body


def recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("connection closed")
        buf += chunk
    return bytes(buf)


def read_packet(sock):
    """(type, flags, body) of the next control packet on sock. Shared with the test broker."""
    first = recv_exact(sock, 1)[0]
    length, shift = 0, 0
    while True:
        d = recv_exact(sock, 1)[0]
        length |= (d & 0x7F) << shift
        shift += 7
        if not d & 0x80:
            break
        if shift > 21:
            raise MqttError("bad remaining length")
    return first >> 4, first & 0x0F, recv_exact(sock, length) if length else b""


def parse_publish(flags, body):
    """(topic, payload, qos, packet id or None) of a PUBLISH body."""
    qos = (flags >> 1) & 3
    tlen = struct.unpack(">H", body[:2])[0]
    topic = body[2:2 + tlen].decode("utf-8", "replace")
    pos = 2 + tlen
    pid = None
    if qos:
        pid = struct.unpack(">H", body[pos:pos + 2])[0]
        pos += 2
    return topic, body[pos:], qos, pid


class MqttClient:
    """publish() and subscribe() may be called from any thread. on_connect() runs on the client's
    thread after every successful connection (subscriptions are already re-sent); on_message(topic,
    payload) for every PUBLISH received. Both must not block for long."""

    def __init__(self, host, port=1883, username="", password="", client_id="matchy", keepalive=60,
                 will=None, log=print):
        self.host, self.port, self.username, self.password = host, int(port), username or "", password or ""
        self.client_id, self.keepalive, self.will, self.log = client_id, int(keepalive), will, log
        self.on_connect = self.on_message = None
        self.subscriptions = []
        self.sock = None
        self.connected = False
        self.wlock = threading.Lock()
        self.pid = 0
        self.stopping = threading.Event()

    # ---------------- one connection ----------------
    def connect(self, timeout=10):
        """One attempt: TCP, CONNECT, CONNACK, then the subscriptions. Raises OSError or MqttError."""
        sock = socket.create_connection((self.host, self.port), timeout)
        try:
            flags, payload = 0x02, _str(self.client_id)  # clean session
            if self.will:
                topic, message, retain = self.will
                flags |= 0x04 | (0x20 if retain else 0)
                payload += _str(topic) + _str(message)
            if self.username:
                flags |= 0x80
                payload += _str(self.username)
                if self.password:
                    flags |= 0x40
                    payload += _str(self.password)
            body = _str("MQTT") + bytes([4, flags]) + struct.pack(">H", self.keepalive) + payload
            sock.sendall(packet(CONNECT, 0, body))
            ptype, _flags, data = read_packet(sock)
            if ptype != CONNACK or len(data) < 2:
                raise MqttError("the broker did not answer with CONNACK")
            if data[1]:
                raise MqttError("connection refused: " + CONNACK_REASONS.get(data[1], "code %d" % data[1]))
            # Half the keep-alive of silence sends a ping; a second one with no answer drops the line.
            sock.settimeout(max(self.keepalive / 2.0, 5))
        except BaseException:
            sock.close()
            raise
        self.sock, self.connected = sock, True
        for filt in list(self.subscriptions):
            self._subscribe(filt)
        if self.on_connect:
            self.on_connect()

    def loop(self):
        """Read until the connection drops (raises OSError or MqttError)."""
        sock, pinged = self.sock, False
        while not self.stopping.is_set():
            try:
                ptype, flags, body = read_packet(sock)
            except socket.timeout:
                if pinged:
                    raise OSError("no answer to the keep-alive ping")
                self._send(packet(PINGREQ, 0))
                pinged = True
                continue
            pinged = False
            if ptype == PUBLISH:
                topic, payload, qos, pid = parse_publish(flags, body)
                if qos == 1:
                    self._send(packet(PUBACK, 0, struct.pack(">H", pid)))
                if self.on_message:
                    try:
                        self.on_message(topic, payload)
                    except Exception as e:  # a bad command must not cost the connection
                        self.log("mqtt: handling %s failed: %r" % (topic, e))
            # CONNACK, PUBACK, SUBACK and PINGRESP carry nothing we act on at QoS 0.

    def _send(self, data):
        with self.wlock:
            if not self.sock:
                raise OSError("not connected")
            self.sock.sendall(data)

    def _drop(self):
        with self.wlock:
            sock, self.sock, self.connected = self.sock, None, False
        if sock:
            try:
                sock.close()
            except OSError:
                pass

    # ---------------- what callers use ----------------
    def publish(self, topic, payload=b"", retain=False):
        """QoS 0. False when not connected or the write failed; on_connect is the place to republish."""
        if not self.connected:
            return False
        try:
            self._send(packet(PUBLISH, 0x01 if retain else 0, _str(topic) + _bytes(payload)))
            return True
        except OSError as e:
            self.log("mqtt: publish failed: %s" % e)
            self._drop()
            return False

    def subscribe(self, *filters):
        for filt in filters:
            if filt not in self.subscriptions:
                self.subscriptions.append(filt)
            if self.connected:
                try:
                    self._subscribe(filt)
                except OSError:
                    self._drop()

    def _subscribe(self, filt):
        self.pid = self.pid % 65535 + 1
        self._send(packet(SUBSCRIBE, 0x02, struct.pack(">H", self.pid) + _str(filt) + b"\x00"))

    def disconnect(self):
        """A clean DISCONNECT (the broker then drops the will), then close."""
        try:
            self._send(packet(DISCONNECT, 0))
        except OSError:
            pass
        self._drop()

    def run(self):
        """Connect, serve, reconnect with a backoff, until stop()."""
        delay = FIRST_RETRY_S
        while not self.stopping.is_set():
            try:
                self.connect()
                self.log("mqtt: connected to %s:%d" % (self.host, self.port))
                delay = FIRST_RETRY_S
                self.loop()
            except (OSError, MqttError) as e:
                if not self.stopping.is_set():
                    self.log("mqtt: %s; trying again in %d s" % (e, delay))
            except Exception:
                import traceback
                traceback.print_exc()
            finally:
                self._drop()
            if self.stopping.wait(delay):
                break
            delay = min(delay * 2, MAX_RETRY_S)

    def start(self):
        threading.Thread(target=self.run, name="mqtt", daemon=True).start()

    def stop(self):
        self.stopping.set()
        self.disconnect()


def publish_once(host, port, username, password, client_id, messages, timeout=10):
    """Connect, publish `messages` ([(topic, payload, retain)]), disconnect. For clean-up work that
    has no business keeping a connection. Raises OSError or MqttError."""
    c = MqttClient(host, port, username, password, client_id=client_id, log=lambda *a: None)
    c.connect(timeout)
    try:
        for topic, payload, retain in messages:
            c._send(packet(PUBLISH, 0x01 if retain else 0, _str(topic) + _bytes(payload)))
        time.sleep(0.2)  # let the writes leave before the socket closes
    finally:
        c.disconnect()
