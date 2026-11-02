"""A stdlib ACP agent for tests/test_f46a_acp.py (F4-6a): speaks JSON-RPC 2.0 lines on stdin/stdout and behaves as
the scenario in argv[1] says. Never used outside the tests."""

import json
import os
import sys

SCENARIO = sys.argv[1] if len(sys.argv) > 1 else "text"
_next = [1000]


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def read():
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    return json.loads(line)


def call(method, params):
    """A request to the client; returns its response (notifications from it are not expected here)."""
    _next[0] += 1
    rid = _next[0]
    send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
    while True:
        m = read()
        if m.get("id") == rid and "method" not in m:
            return m


def chunk(sid, text):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": {
        "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}}})


def permission(sid, kind, path, title="İşlem"):
    tc = {"toolCallId": "c1", "title": title, "kind": kind, "status": "pending",
          "locations": [{"path": path}] if path else [], "rawInput": {"command": "ls -la"} if kind == "execute" else None}
    r = call("session/request_permission", {"sessionId": sid, "toolCall": tc, "options": [
        {"optionId": "always", "name": "Her zaman", "kind": "allow_always"},
        {"optionId": "once", "name": "Bir kez", "kind": "allow_once"},
        {"optionId": "no", "name": "Hayır", "kind": "reject_once"}]})
    return r.get("result", {}).get("outcome", {})


def prompt_turn(sid, cwd, text):
    if SCENARIO == "text":
        chunk(sid, "Merhaba ")
        chunk(sid, "dünya")
        send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": {
            "sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Oku", "kind": "read",
            "locations": [{"path": os.path.join(cwd, "a.txt")}]}}})
        send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": {
            "sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed"}}})
        return "end_turn"
    if SCENARIO == "fs":
        r = call("fs/read_text_file", {"sessionId": sid, "path": os.path.join(cwd, "a.txt"), "line": 2, "limit": 1})
        chunk(sid, "okundu=%s;" % r.get("result", {}).get("content", "").strip())
        w = call("fs/write_text_file", {"sessionId": sid, "path": os.path.join(cwd, "alt", "out.txt"),
                                        "content": "yazıldı"})
        chunk(sid, "yazma=%s;" % ("ok" if "result" in w else w["error"]["message"]))
        for bad in ("/etc/passwd", os.path.join(cwd, "..", "kacak.txt"), os.path.join(cwd, ".env"), "rel.txt"):
            e = call("fs/write_text_file", {"sessionId": sid, "path": bad, "content": "x"})
            chunk(sid, "dış=%s;" % ("ok" if "result" in e else "red"))
        return "end_turn"
    if SCENARIO.startswith("perm_"):
        kind, path = SCENARIO[5:], os.path.join(cwd, "a.txt")
        if kind == "outside":
            kind, path = "read", "/etc/hosts"
        elif kind == "execute":
            path = None
        out = permission(sid, kind, path)
        chunk(sid, "izin=%s" % (out.get("optionId") or out.get("outcome")))
        return "end_turn"
    if SCENARIO == "terminal":
        r = call("terminal/create", {"sessionId": sid, "command": "ls"})
        chunk(sid, "terminal=%s" % r.get("error", {}).get("code"))
        return "end_turn"
    if SCENARIO == "env":
        chunk(sid, "env=%s" % ("VAR" if os.environ.get("PIXEL_TEST_PROVIDER_KEY") else "YOK"))
        return "end_turn"
    if SCENARIO == "garbage":
        for _ in range(30):
            sys.stdout.write("bu json değil\n")
        sys.stdout.flush()
        return "end_turn"
    if SCENARIO == "big":
        chunk(sid, "x" * (1024 * 1024 + 10))
        return "end_turn"
    if SCENARIO == "refusal":
        return "refusal"
    if SCENARIO in ("hang", "cancel_ok"):
        chunk(sid, "başladım")
        while True:
            m = read()
            if m.get("method") == "session/cancel" and SCENARIO == "cancel_ok":
                return "cancelled"
    return "end_turn"


def main():
    while True:
        m = read()
        method, rid, p = m.get("method"), m.get("id"), m.get("params") or {}
        if method == "initialize":
            assert p["clientCapabilities"]["fs"] == {"readTextFile": True, "writeTextFile": True}
            assert p["clientCapabilities"].get("terminal") is not True
            res = {"protocolVersion": 2 if SCENARIO == "v2" else 1, "agentCapabilities": {}}
            if SCENARIO == "auth":
                res["authMethods"] = [{"id": "login", "name": "Giriş"}]
            send({"jsonrpc": "2.0", "id": rid, "result": res})
        elif method == "session/new":
            assert p["mcpServers"] == [] and os.path.isabs(p["cwd"])
            if SCENARIO == "auth":
                send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "Authentication required"}})
                continue
            if SCENARIO == "slow_setup":
                continue                                    # never answers
            send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": "sess_1"}})
        elif method == "session/prompt":
            text = p["prompt"][0]["text"]
            reason = prompt_turn(p["sessionId"], os.getcwd(), text)
            send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": reason}})


if __name__ == "__main__":
    main()
