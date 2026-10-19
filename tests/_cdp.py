"""Minimal Chrome DevTools Protocol driver for UI tests (stdlib only; WebSocket framing from pixel_connector/ws.py).

    with Chrome() as chrome:                       # headless, temp profile (deleted on exit)
        page = chrome.page(url, width=1280, height=900)
        page.eval("document.title")
        page.wait("!!document.querySelector('#btn-devices:not([hidden])')")
        page.click("#btn-devices")
        page.screenshot("/tmp/x.png")
        page.errors                                # console errors + uncaught exceptions

Each Chrome() is a separate browser profile (fresh IndexedDB/localStorage), so two instances are two "browsers".
"""

import base64
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request

import _helpers  # noqa: F401  (sys.path)
from pixel_connector import ws as W

CHROME = next((shutil.which(n) for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
               if shutil.which(n)), None)


class CDPError(Exception):
    pass


class Page:
    def __init__(self, ws_url: str) -> None:
        self.ws = W.connect(ws_url, timeout=10, max_message=256 * 1024 * 1024)
        self._id = 0
        self._lock = threading.Lock()
        self._waiters = {}
        self._results = {}
        self.events = []
        self.errors = []
        self.console = []
        self._alive = True
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        self.send("Runtime.enable")
        self.send("Page.enable")
        self.send("Log.enable")

    def _read(self) -> None:
        while self._alive:
            try:
                m = self.ws.recv()
            except (W.ConnectionClosed, OSError):
                self._alive = False
                for ev in list(self._waiters.values()):
                    ev.set()
                return
            try:
                msg = json.loads(m)
            except ValueError:
                continue
            if "id" in msg:
                ev = self._waiters.pop(msg["id"], None)
                self._results[msg["id"]] = msg
                if ev:
                    ev.set()
                continue
            meth = msg.get("method")
            p = msg.get("params") or {}
            if meth == "Runtime.exceptionThrown":
                d = p.get("exceptionDetails") or {}
                ex = d.get("exception") or {}
                self.errors.append("exception: %s %s" % (d.get("text"), ex.get("description") or ""))
            elif meth == "Runtime.consoleAPICalled":
                args = " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args") or [])
                self.console.append((p.get("type"), args))
                if p.get("type") == "error":
                    self.errors.append("console.error: " + args)
            elif meth == "Log.entryAdded":
                e = p.get("entry") or {}
                if e.get("level") == "error":
                    self.errors.append("log: %s %s" % (e.get("text"), e.get("url") or ""))
            elif meth == "Page.javascriptDialogOpening":
                # window.confirm in the UI: accept automatically
                threading.Thread(target=self.send, args=("Page.handleJavaScriptDialog", {"accept": True}),
                                 daemon=True).start()

    def send(self, method, params=None, timeout=30.0):
        with self._lock:
            self._id += 1
            mid = self._id
            ev = threading.Event()
            self._waiters[mid] = ev
            self.ws.send_text(json.dumps({"id": mid, "method": method, "params": params or {}}))
        if not ev.wait(timeout):
            raise CDPError("timeout: %s" % method)
        msg = self._results.pop(mid, None)
        if msg is None:
            raise CDPError("connection closed")
        if "error" in msg:
            raise CDPError("%s: %s" % (method, msg["error"]))
        return msg.get("result") or {}

    def eval(self, expr, timeout=30.0):
        """Evaluate an expression (awaits promises) and return its JSON value."""
        r = self.send("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True,
                                           "userGesture": True}, timeout)
        if r.get("exceptionDetails"):
            d = r["exceptionDetails"]
            raise CDPError("JS error: %s %s" % (d.get("text"), (d.get("exception") or {}).get("description")))
        return (r.get("result") or {}).get("value")

    def wait(self, expr, timeout=20.0, interval=0.1, what=None):
        end = time.time() + timeout
        last = None
        while time.time() < end:
            try:
                last = self.eval(expr)
            except CDPError as e:
                last = str(e)
            if last:
                return last
            time.sleep(interval)
        raise AssertionError("timeout waiting for %s (last: %r)" % (what or expr, last))

    def navigate(self, url, wait_load=True):
        self.send("Page.navigate", {"url": url})
        if wait_load:
            self.wait("document.readyState === 'complete'", 20)

    def viewport(self, width, height, mobile=False, scale=1):
        self.send("Emulation.setDeviceMetricsOverride", {"width": width, "height": height,
                                                          "deviceScaleFactor": scale, "mobile": mobile})
        if mobile:
            self.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})

    def click(self, selector):
        ok = self.eval("(() => { const e = document.querySelector(%s); if (!e) return false; e.scrollIntoView({block:'center'}); e.click(); return true; })()"
                       % json.dumps(selector))
        if not ok:
            raise AssertionError("no element " + selector)

    def type(self, selector, text):
        """Set an input/textarea value as a user would (input event)."""
        ok = self.eval("(() => { const e = document.querySelector(%s); if (!e) return false; e.focus(); e.value = %s;"
                       " e.dispatchEvent(new Event('input', {bubbles: true})); return true; })()"
                       % (json.dumps(selector), json.dumps(text)))
        if not ok:
            raise AssertionError("no element " + selector)

    def text(self, selector):
        return self.eval("(() => { const e = document.querySelector(%s); return e ? e.textContent : null; })()"
                         % json.dumps(selector))

    def screenshot(self, path, full=False):
        params = {"format": "png"}
        if full:
            m = self.send("Page.getLayoutMetrics")
            cs = m.get("cssContentSize") or m.get("contentSize")
            params["clip"] = {"x": 0, "y": 0, "width": cs["width"], "height": cs["height"], "scale": 1}
            params["captureBeyondViewport"] = True
        data = self.send("Page.captureScreenshot", params, 60)["data"]
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return path

    def close(self):
        self._alive = False
        try:
            self.ws.close(1000, "", wait=0.2)
        except Exception:
            pass


class Chrome:
    def __init__(self, extra_args=None) -> None:
        if not CHROME:
            raise RuntimeError("Chrome not installed")
        self.profile = tempfile.mkdtemp(prefix="pixel-cdp-")
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
             "--disable-extensions", "--disable-background-networking", "--disable-sync",
             "--disable-features=Translate,MediaRouter", "--hide-scrollbars", "--mute-audio",
             "--user-data-dir=" + self.profile, "--remote-debugging-port=0", "--remote-allow-origins=*",
             "--window-size=1280,900"] + list(extra_args or []) + ["about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port_file = os.path.join(self.profile, "DevToolsActivePort")
        end = time.time() + 20
        while time.time() < end and not os.path.exists(port_file):
            time.sleep(0.05)
        time.sleep(0.1)
        with open(port_file) as f:
            self.port = int(f.readline().strip())
        self.pages = []

    def _json(self, path, method="GET"):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method=method)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"null")

    def page(self, url=None, width=1280, height=900, mobile=False):
        targets = [t for t in self._json("/json/list") if t.get("type") == "page"]
        if self.pages or not targets:
            t = self._json("/json/new?about:blank", method="PUT")
        else:
            t = targets[0]
        p = Page(t["webSocketDebuggerUrl"])
        p.viewport(width, height, mobile)
        self.pages.append(p)
        if url:
            p.navigate(url)
        return p

    def close(self):
        for p in self.pages:
            p.close()
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(5)
        shutil.rmtree(self.profile, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
