"""Where the add-on's own downloads may go. A remote source you configure (a manifest, the daily
picture, an art API) is fetched wherever it is, your own network included. What it hands back is
not trusted that far: a redirect may stay on the configured host or go to a public address, and an
address an art API names must be public too. Otherwise a source answering `302` to the Supervisor,
Home Assistant or a router would have the add-on fetch that and serve it at /img/<id>/original.jpg.

Two checks: the name is resolved before the request (a clear refusal, no connection made), and the
address the connection actually reached is checked before anything is sent, on every hop, so a name
that answers public for the first and private for the second lookup (DNS rebinding) is refused too."""
import http.client
import ipaddress
import socket
import urllib.parse
import urllib.request


def ip_is_public(ip):
    ip = ipaddress.ip_address(ip)
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    # is_global leaves out private, loopback, link-local, shared (100.64/10), reserved and unspecified
    return ip.is_global and not ip.is_multicast


def host_is_public(host):
    """True when every address the name resolves to is public; False for a private one or no answer."""
    host = (host or "").strip("[]")
    try:
        return ip_is_public(host)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    return bool(infos) and all(ip_is_public(i[4][0].split("%")[0]) for i in infos)


def check_url(url, trusted, what="address"):
    """A ValueError unless url is http(s) on a trusted host (one you configured) or a public one."""
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https"):
        raise ValueError("%s refused: only http(s) URLs are fetched (%r)" % (what, url[:200]))
    host = (u.hostname or "").lower()
    if host in trusted:
        return
    if not host_is_public(host):
        raise ValueError("%s refused: %s is not a public address" % (what, host or "no host"))


class GuardedRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, trusted):
        self.trusted = trusted

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(urllib.parse.urljoin(req.full_url, newurl), self.trusted, "redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def guarded_connection(base, trusted):
    """base (HTTPConnection or HTTPSConnection) that refuses, before sending anything (TLS included),
    a connection to a host not in `trusted` that reached a non-public address."""
    class Guarded(base):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._create_connection = self._connect_checked

        def _connect_checked(self, address, *a, **kw):
            sock = socket.create_connection(address, *a, **kw)
            if (self.host or "").lower() not in trusted:
                ip = sock.getpeername()[0].split("%")[0]
                if not ip_is_public(ip):
                    sock.close()
                    raise ValueError("connection refused: %s reached %s, not a public address" % (self.host, ip))
            return sock
    return Guarded


class GuardedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, trusted):
        super().__init__()
        self.conn = guarded_connection(http.client.HTTPConnection, trusted)

    def http_open(self, req):
        return self.do_open(self.conn, req)


class GuardedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, trusted):
        super().__init__()
        self.conn = guarded_connection(http.client.HTTPSConnection, trusted)

    def https_open(self, req):  # the name stays the Host header, the SNI and the certificate's
        return self.do_open(self.conn, req, context=self._context)


def trusted_hosts(*urls):
    return {(urllib.parse.urlsplit(u).hostname or "").lower() for u in urls if u} - {""}


def urlopen(req, trusted, timeout):
    """urllib.request.urlopen with every redirect, and the address every connection reaches, checked
    against `trusted` (a set of host names)."""
    return urllib.request.build_opener(GuardedRedirects(trusted), GuardedHTTPHandler(trusted),
                                       GuardedHTTPSHandler(trusted)).open(req, timeout=timeout)
