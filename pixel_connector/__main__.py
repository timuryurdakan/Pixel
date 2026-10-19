"""pixel-connector CLI (V3.md §5.3). Output is Turkish, flags are English.

    pixel-connector setup --server https://… --room <rid> --key <writeKey> --name Timur [--no-default-workspace]
    pixel-connector run
    pixel-connector pair
    pixel-connector peers [list|revoke <bid>|revoke-all]
    pixel-connector agents [list|delete <key>]
    pixel-connector runtimes [list|start <id>|stop <id>]
    pixel-connector usage [--period day|week|month]
    pixel-connector workspace [list|add <yol> [--name AD] [--id KİMLİK] [--children]|remove <kimlik>]
    pixel-connector approve [<id>|--link] | deny <id>
    pixel-connector doctor | config-check | install-service [--dry-run] | uninstall-service [--dry-run] | reset-keys
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import stat
import sys
import threading
import time
from typing import Callable, Optional

from . import __version__
from . import config as CFG
from .approvals import ctl_request

OUT = print


def _home(args) -> str:
    return CFG.ensure_dir(CFG.connector_home(getattr(args, "home", None)))


def _err(msg: str) -> int:
    print("Hata: " + msg, file=sys.stderr)
    return 1


# --------------------------------------------------------------------------------------------------
# setup / config-check
# --------------------------------------------------------------------------------------------------

def cmd_setup(args) -> int:
    home = _home(args)
    if not re.match(r"^[a-z2-7]{26}\Z", args.room or ""):
        return _err("--room 26 karakterlik oda kimliği olmalı.")
    path = os.path.join(home, CFG.CONFIG_FILE)
    cur = CFG.read_json(path)
    claude = shutil.which("claude")
    if isinstance(cur, dict):
        cur.update({"server": args.server.rstrip("/"), "room": args.room, "key": args.key, "owner": args.name})
        if args.device_name:
            cur["deviceName"] = args.device_name
        data = cur
    else:
        data = CFG.initial_config(args.server, args.room, args.key, args.name, args.device_name,
                                  os.path.abspath(claude) if claude else None)
    _, errors, _ = CFG.validate(data)
    errors = [e for e in errors if not e.startswith("workspaces")]
    if errors:
        for e in errors:
            print("  - " + e, file=sys.stderr)
        return _err("ayarlar geçersiz, connector.json yazılmadı.")
    ws_note = _default_workspace(data, args.no_default_workspace)
    CFG.write_json(path, data)
    from .peers import DeviceKey
    key = DeviceKey.load_or_create(home)
    OUT("Ayarlar yazıldı: %s" % path)
    OUT("Cihaz kimliği: %s" % key.device_id)
    if not claude:
        OUT("Not: `claude` bulunamadı. Claude Code kullanacaksanız connector.json → engines.claude.path ayarlayın.")
    OUT(ws_note)
    return 0


def _default_workspace(data: dict, opt_out: bool) -> str:
    """Phase 17: add ~/PixelOfficeIsler as the default workspace when the list is empty. Never touches a non-empty
    list (re-running setup keeps the user's workspaces as they are). Returns a line for the user."""
    from . import workspaces as W
    wss = data.get("workspaces")
    if isinstance(wss, list) and wss:
        return "Çalışma klasörleri: %d tane (değiştirilmedi). Liste: pixel-connector workspace list" % len(wss)
    if opt_out:
        return ("Varsayılan çalışma klasörü eklenmedi (--no-default-workspace). Eklemek için: "
                "pixel-connector workspace add ~/projem")
    try:
        real = W.ensure_default_dir()
    except (W.WorkspaceDenied, OSError) as e:
        return ("Uyarı: varsayılan çalışma klasörü eklenemedi (%s). Elle ekleyin: pixel-connector workspace add ~/projem"
                % term_safe(e))
    data["workspaces"] = [dict(W.DEFAULT_ENTRY)]
    return ("Varsayılan çalışma klasörü: %s (kimlik %s, alt klasörler açık). Claude Code ajanları burada çalışır; "
            "başka klasör için: pixel-connector workspace add ~/projem" % (real, W.DEFAULT_ENTRY["id"]))


def cmd_config_check(args) -> int:
    home = _home(args)
    cfg = CFG.load_config(home)
    for w in cfg.warnings:
        OUT("Uyarı: " + w)
    if cfg.errors:
        for e in cfg.errors:
            OUT("Hata: " + e)
        OUT("connector.json geçersiz (%d hata)." % len(cfg.errors))
        return 1
    d = cfg.data
    OUT("connector.json geçerli. Oda: %s · Sunucu: %s · Cihaz adı: %s" % (d["room"], d["server"], d["deviceName"]))
    OUT("Çalışma klasörleri:")
    if not d["workspaces"]:
        OUT("  (yok) — Claude Code ajanları için en az bir klasör ekleyin.")
    for w in d["workspaces"]:
        OUT("  %-12s %-16s %s%s" % (w["id"], w["name"], w["real"], "  (alt klasörler açık)" if w["children"] else ""))
    OUT("Çalışma ortamları:")
    if not d["runtimes"]:
        OUT("  (yok)")
    for r in d["runtimes"]:
        OUT("  %-12s %-20s başlat=%s durdur=%s" % (r["id"], r["name"], r["start"], r["stop"]))
    c = d["engines"]["claude"]
    OUT("Claude Code: %s" % (c["path"] or "bulunamadı"))
    OUT("Otomatik profil: %s · Görev onayı: %s" % ("açık" if d["profiles"]["auto"]["enabled"] else "kapalı",
                                                   d["confirm"]["task"]))
    if d["workspaces"]:
        OUT("Uyarı: Claude Code bu klasörlerdeki proje ayarlarını (hook'lar, .mcp.json) güven sorusu sormadan çalıştırır. "
            "Yalnızca güvendiğiniz klasörleri ekleyin.")
    return 0


# --------------------------------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------------------------------

class InstanceLock:
    def __init__(self, path: str) -> None:
        self.path = path
        self.f = None

    def acquire(self) -> bool:
        self.f = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.f.close()
            self.f = None
            return False
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return True

    def release(self) -> None:
        if self.f is not None:
            self.f.close()
            self.f = None


def cmd_run(args) -> int:
    home = _home(args)
    cfg = CFG.load_config(home)
    if not cfg.ok:
        for e in cfg.errors:
            print("Hata: " + e, file=sys.stderr)
        return 2
    lock = InstanceLock(os.path.join(home, CFG.LOCK_FILE))
    if not lock.acquire():
        return _err("bağlayıcı zaten çalışıyor (run.lock).")
    from .app import Connector
    stop = threading.Event()

    def _sig(signum, frame):
        stop.set()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(s, _sig)
        except (ValueError, OSError):
            pass
    conn = Connector(home, cfg, log=lambda m: print(m, file=sys.stderr, flush=True))
    conn.start()
    OUT("Pixel Office bağlayıcı %s çalışıyor. Cihaz: %s (%s)" % (__version__, cfg.data["deviceName"],
                                                                conn.key.device_id), flush=True)
    last = None
    try:
        while not stop.wait(1.0):
            if conn.relay.status != last:
                last = conn.relay.status
                OUT("Röle: %s" % last, flush=True)
    finally:
        OUT("Kapatılıyor…", flush=True)
        conn.stop()
        lock.release()
    return 0


# --------------------------------------------------------------------------------------------------
# pair
# --------------------------------------------------------------------------------------------------

def ask_yes_no(prompt: str, timeout: float = 60.0) -> bool:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        if os.name != "nt" and sys.stdin.isatty():
            import select
            r, _, _ = select.select([sys.stdin], [], [], timeout)
            if not r:
                OUT("\nSüre doldu.")
                return False
        line = sys.stdin.readline()
    except (OSError, KeyboardInterrupt):
        return False
    return line.strip().lower() in ("e", "evet", "y", "yes")


def _print_code(info: dict) -> None:
    OUT("")
    OUT("Eşleştirme kodu:  %s   (10 dk geçerli, tek deneme)" % info["code"])
    OUT("Tarayıcıda: Cihazlar → Cihaz ekle → bu kodu girin.")
    OUT("Bekleniyor…")


def _confirm_text(info: dict) -> str:
    return ("\nTarayıcı: %s (bid %s…)\nDoğrulama kodu: %s\nTarayıcıda aynı kod görünüyor mu? Onaylıyor musunuz? [e/H] "
            % (info.get("label") or "?", (info.get("bid") or "")[:6], info.get("sas")))


def _pair_result(info: dict) -> int:
    st, reason = info.get("state"), info.get("reason")
    if st == "done":
        OUT("Eşleşti. Bu tarayıcı artık bu cihaza görev verebilir.")
        return 0
    if reason == "code":
        OUT("Kod yanlış ya da araya girildi. Yeni kod üretin: pixel-connector pair")
    elif reason == "refused":
        OUT("Eşleştirme reddedildi.")
    elif st == "expired" or reason in ("expired", "timeout"):
        OUT("Süre doldu. Yeni kod üretin: pixel-connector pair")
    else:
        OUT("Eşleştirme tamamlanmadı (%s)." % (reason or st))
    return 1


def cmd_pair(args, ask: Callable[[str], bool] = ask_yes_no) -> int:
    home = _home(args)
    try:
        st, info = ctl_request(home, "POST", "/ctl/pair/start")
    except OSError:
        return _pair_inprocess(home, ask)
    if st == 503:
        return _err("bağlayıcı röleye bağlı değil. Ağ bağlantısını ve `pixel-connector doctor` çıktısını kontrol edin.")
    if st != 200:
        return _err("eşleştirme başlatılamadı (%s)." % st)
    _print_code(info)
    while True:
        st, info = ctl_request(home, "GET", "/ctl/pair/status?wait=25&until=confirm,done,failed,expired", timeout=35)
        if st != 200:
            return _err("bağlayıcı yanıt vermedi.")
        if info["state"] == "confirm":
            ok = ask(_confirm_text(info))
            st, info = ctl_request(home, "POST", "/ctl/pair/confirm", {"ok": ok})
            return _pair_result(info if st == 200 else {"state": "failed", "reason": "timeout"})
        if info["state"] in ("done", "failed", "expired"):
            return _pair_result(info)


def _pair_inprocess(home: str, ask: Callable[[str], bool]) -> int:
    """No service running: a temporary in-process session that only pairs."""
    cfg = CFG.load_config(home)
    if not cfg.ok:
        for e in cfg.errors:
            print("Hata: " + e, file=sys.stderr)
        return 2
    lock = InstanceLock(os.path.join(home, CFG.LOCK_FILE))
    if not lock.acquire():
        return _err("bağlayıcı çalışıyor ama denetim arayüzüne ulaşılamadı (control.json).")
    from .app import Connector
    OUT("Servis çalışmıyor; geçici bir oturumla eşleştiriliyor…")
    conn = Connector(home, cfg, start_ctl=False)
    conn.start()
    try:
        if not conn.relay.connected.wait(20):
            return _err("röleye bağlanılamadı (%s)." % (conn.relay.last_error or conn.relay.status))
        p = conn.router.start_pairing()
        _print_code(p.info())
        state = p.wait({"confirm", "done", "failed", "expired"}, p.expires - time.time() + 5)
        if state == "confirm":
            p.confirm(ask(_confirm_text(p.info())))
            time.sleep(0.5)
        return _pair_result(p.info())
    finally:
        conn.stop()
        lock.release()


# --------------------------------------------------------------------------------------------------
# small service commands
# --------------------------------------------------------------------------------------------------

def _service_call(home, method, path, body=None):
    try:
        return ctl_request(home, method, path, body)
    except OSError:
        print("Bağlayıcı çalışmıyor. Önce: systemctl --user start pixel-connector (ya da pixel-connector run)",
              file=sys.stderr)
        return None, None


def cmd_peers(args) -> int:
    home = _home(args)
    if args.action in ("revoke", "revoke-all"):
        body = {"all": True} if args.action == "revoke-all" else {"bid": args.target}
        st, d = _service_call(home, "POST", "/ctl/peers/revoke", body)
        if st is None:
            from .peers import PeerStore      # service down: edit peers.json directly
            ps = PeerStore(home)
            bids = ps.revoke_all() if args.action == "revoke-all" else ([args.target] if ps.revoke(args.target) else [])
            from .audit import AuditLog
            for b in bids:
                AuditLog(os.path.join(home, CFG.AUDIT_FILE)).write("peer.revoke", bid=b, by="local")
            d, st = {"revoked": bids}, 200 if bids else 404
        if st != 200:
            return _err("böyle bir eşleşme yok.")
        OUT("Kaldırıldı: %s" % (", ".join(d["revoked"]) or "(yok)"))
        return 0
    from .peers import PeerStore
    peers = PeerStore(home).public_list()
    if not peers:
        OUT("Eşleşmiş tarayıcı yok.")
    for p in peers:
        OUT("%s  %-24s eşleşme %s  son görülme %s" % (p["bid"], p["label"], _fmt_ms(p["pairedAt"]), _fmt_ms(p["lastSeen"])))
    return 0


def _fmt_ms(ms) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ms / 1000)) if ms else "-"


def cmd_agents(args) -> int:
    home = _home(args)
    if args.action == "delete":
        st, d = _service_call(home, "POST", "/ctl/agents/delete", {"agent": args.target})
        if st != 200:
            return _err("ajan silinemedi.")
        OUT("Silindi: %s" % args.target)
        return 0
    from .agents import AgentStore
    agents = AgentStore(home).list()
    if not agents:
        OUT("Ajan yok. Ajanlar ofisten (Cihazlar → Yeni ajan) oluşturulur.")
    for a in agents:
        OUT("%-8s %-24s %-7s %-10s %s" % (a["key"], a["name"], a["engine"], a.get("profile"),
                                          (a.get("workspace") or {}).get("id") or "-"))
    return 0


def cmd_runtimes(args) -> int:
    home = _home(args)
    if args.action in ("start", "stop"):
        st, d = _service_call(home, "POST", "/ctl/runtimes/" + args.action, {"runtime": args.target})
        if st is None:
            return 1
        if st != 200:
            return _err("bu ortam listede yok: %s" % args.target)
        OUT("%s: %s (çıkış kodu %s)%s" % (args.target, "tamam" if d["ok"] else "başarısız", d["exitCode"],
                                          (" — " + d["message"]) if d.get("message") else ""))
        return 0 if d["ok"] else 1
    cfg = CFG.load_config(home)
    from .runtimes import Runtimes
    rts = Runtimes(cfg.data.get("runtimes") or []).list() if cfg.ok else []
    if not rts:
        OUT("Tanımlı çalışma ortamı yok (connector.json → runtimes).")
    for r in rts:
        OUT("%-12s %-22s %s%s" % (r["id"], r["name"], r["state"], (" " + r["detail"]) if r["detail"] else ""))
    return 0


def cmd_usage(args) -> int:
    home = _home(args)
    from .usage import Ledger
    led = Ledger(os.path.join(home, CFG.LEDGER_FILE))
    t = led.totals(args.period)
    names = {"day": "Bugün", "week": "Bu hafta", "month": "Bu ay"}
    OUT("%s: %d görev · %s token (ağırlıklı) · ≈ %.2f $ (tahmini)" % (names[args.period], t["tasks"],
                                                                  format(t["tokens"], ",").replace(",", "."),
                                                                  t["costUsd"]))
    for a in led.by_agent(args.period):
        OUT("  %-8s %12s token  ≈ %.2f $" % (a["agent"], format(a["tokens"], ",").replace(",", "."), a["costUsd"]))
    led.close()
    return 0


def term_safe(text, limit: int = 200) -> str:
    """Remote-supplied text for the terminal: control characters (ESC sequences, CR, …) become spaces (Phase 15)."""
    return "".join(ch if ch.isprintable() else " " for ch in str(text or ""))[:limit]


def cmd_approve(args, ok: bool = True) -> int:
    home = _home(args)
    if ok and (args.link or not args.id):
        st, d = _service_call(home, "GET", "/ctl/approvals")
        if st != 200:
            return 1
        if args.link:
            OUT("Onay sayfası (yalnızca bu bilgisayarda açın): %s" % d["link"])
            return 0
        if not d["pending"]:
            OUT("Onay bekleyen bir şey yok.")
        for p in d["pending"]:
            OUT("%s  %s  %s\n    %s" % (term_safe(p["id"], 16), term_safe(p["agent"], 40), term_safe(p["title"], 80),
                                         term_safe(p["preview"])))
        return 0
    st, d = _service_call(home, "POST", "/ctl/approvals/resolve", {"id": args.id, "ok": ok})
    if st != 200:
        return _err("böyle bekleyen bir onay yok: %s" % args.id)
    OUT("Onaylandı." if ok else "Reddedildi.")
    return 0


# --------------------------------------------------------------------------------------------------
# workspace add|list|remove (Phase 17). Local CLI only: there is no E2E command and no /ctl route for this, so a
# browser can never widen the allow-list (SECURITY_AUDIT.md §5).
# --------------------------------------------------------------------------------------------------

_DENY_TR = {"the home directory itself": "ev dizininin kendisi",
            "the filesystem root": "dosya sisteminin kökü",
            "an ancestor of the home directory": "ev dizinini içeren bir üst klasör"}
_TR_FOLD = str.maketrans("ıİşŞğĞüÜöÖçÇâÂîÎûÛ", "iissgguuoocCaaiiuu")


def _deny_tr(reason: str) -> str:
    if reason.startswith("denied path "):
        return "yasaklı yol " + reason[len("denied path "):]
    return _DENY_TR.get(reason, reason)


def restart_hint(platform: Optional[str] = None) -> str:
    from . import service
    platform = platform or sys.platform
    if platform.startswith("linux"):
        cmd = "systemctl --user restart pixel-connector"
    elif platform == "darwin":
        cmd = "launchctl kickstart -k gui/$(id -u)/" + service.LAUNCHD_LABEL
    elif platform == "win32":
        cmd = 'schtasks /End /TN "%s", ardından schtasks /Run /TN "%s"' % (service.SCHTASK_NAME, service.SCHTASK_NAME)
    else:
        cmd = "pixel-connector run (yeniden başlatın)"
    return ("Değişiklik bağlayıcı yeniden başlayınca geçerli olur: %s\n"
            "(Servis kullanmıyorsanız `pixel-connector run` sürecini durdurup yeniden başlatın.) "
            "Ardından tarayıcıda Cihazlar sayfasını yenileyin." % cmd)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", text.translate(_TR_FOLD).lower()).strip("-_")
    return s[:24].strip("-_") or "klasor"


def _load_raw(home: str):
    path = os.path.join(home, CFG.CONFIG_FILE)
    try:
        raw = CFG.read_json(path)
    except (ValueError, UnicodeDecodeError) as e:
        return path, None, "connector.json geçerli JSON değil (%s)." % e
    if raw is None:
        return path, None, "%s yok. Önce kurulum: pixel-connector setup …" % path
    if not isinstance(raw, dict):
        return path, None, "connector.json bir JSON nesnesi olmalı."
    if raw.get("workspaces") is None:
        raw["workspaces"] = []
    if not isinstance(raw["workspaces"], list):
        return path, None, "connector.json → workspaces bir liste olmalı."
    return path, raw, None


def cmd_workspace(args) -> int:
    from . import workspaces as W
    home = _home(args)
    path, raw, err = _load_raw(home)
    if err:
        return _err(err)
    wss = raw["workspaces"]
    user_home = os.path.expanduser("~")
    if args.action == "list":
        d, errors, warnings = CFG.validate(raw)
        if not d.get("workspaces") and not any(e.startswith("workspaces") for e in errors):
            OUT("Çalışma klasörü yok. Claude Code ajanları için ekleyin: pixel-connector workspace add ~/projem")
            return 0
        OUT("%-12s %-24s %-11s %s" % ("KİMLİK", "AD", "ALT KLASÖR", "YOL"))
        for w in d.get("workspaces") or []:
            OUT("%-12s %-24s %-11s %s%s" % (w["id"], w["name"], "açık" if w["children"] else "kapalı", w["real"],
                                            "" if os.path.isdir(w["real"]) else "  (klasör yok)"))
        for e in errors:
            if e.startswith("workspaces"):
                OUT("Hata: " + e)
        return 1 if any(e.startswith("workspaces") for e in errors) else 0

    if args.action == "remove":
        keep = [w for w in wss if not (isinstance(w, dict) and w.get("id") == args.target)]
        if len(keep) == len(wss):
            return _err("böyle bir çalışma klasörü yok: %s (liste: pixel-connector workspace list)" % term_safe(args.target, 40))
        raw["workspaces"] = keep
        CFG.write_json(path, raw)
        OUT("Kaldırıldı: %s" % args.target)
        users = []
        try:
            from .agents import AgentStore
            users = [a["name"] for a in AgentStore(home).list() if (a.get("workspace") or {}).get("id") == args.target]
        except Exception:
            pass
        if users:
            OUT("Not: bu klasörü kullanan ajanlar artık görev alamaz: %s" % ", ".join(users))
        OUT(restart_hint())
        return 0

    # add
    p = args.target
    if not p or "\x00" in p:
        return _err("klasör yolunu yazın: pixel-connector workspace add ~/projem")
    if not (p == "~" or p.startswith("~/") or p.startswith("~" + os.sep) or os.path.isabs(p)):
        p = os.path.abspath(p)                       # relative to the current directory (local CLI)
    try:
        real = W.expand_path(p, user_home)
    except ValueError:
        return _err("geçersiz yol: %s" % term_safe(args.target))
    reason = W.check_denied(real, user_home)
    if reason:
        return _err("bu klasör çalışma klasörü olamaz: %s (%s)." % (real, _deny_tr(reason)))
    if not os.path.exists(real):
        return _err("klasör yok: %s (önce oluşturun: mkdir -p %s)" % (real, real))
    if not os.path.isdir(real):
        return _err("bu bir klasör değil: %s" % real)
    for w in wss:
        if not isinstance(w, dict):
            continue
        try:
            same = W.expand_path(w.get("path"), user_home) == real
        except ValueError:
            same = False
        if same:
            return _err("bu klasör zaten ekli (kimlik %s)." % term_safe(w.get("id"), 24))
    ids = {w.get("id") for w in wss if isinstance(w, dict)}
    if args.id is not None:
        if not W.ID_RE.match(args.id):
            return _err("--id yalnızca küçük harf, rakam, - ve _ içerebilir (en fazla 24 karakter).")
        if args.id in ids:
            return _err("bu kimlik zaten kullanılıyor: %s" % args.id)
        wid = args.id
    else:
        base = _slug(os.path.basename(real))
        wid, n = base, 2
        while wid in ids:
            suffix = "-%d" % n
            wid, n = base[:24 - len(suffix)] + suffix, n + 1
    name = (args.name if args.name is not None else os.path.basename(real)) or wid
    name = "".join(ch for ch in name if ch.isprintable()).strip()[:40] or wid
    entry = {"id": wid, "name": name, "path": real, "children": bool(args.children)}
    raw["workspaces"] = wss + [entry]
    _, errors, _ = CFG.validate(raw)
    mine = [e for e in errors if e.startswith("workspaces[%d]" % (len(raw["workspaces"]) - 1))]
    if mine:
        return _err("; ".join(mine))
    CFG.write_json(path, raw)
    if _real_differs(p, user_home, real):
        OUT("Not: sembolik bağ çözüldü; gerçek klasör kaydedildi: %s" % real)
    OUT("Eklendi: %s  %s  %s%s" % (wid, name, real, "  (alt klasörler açık)" if entry["children"] else ""))
    OUT("Uyarı: Claude Code bu klasördeki proje ayarlarını (hook'lar, .mcp.json) güven sorusu sormadan çalıştırır. "
        "Yalnızca güvendiğiniz klasörleri ekleyin.")
    OUT(restart_hint())
    return 0


def _real_differs(p: str, user_home: str, real: str) -> bool:
    if p == "~" or p.startswith("~/") or p.startswith("~" + os.sep):
        p = user_home + p[1:]
    return os.path.normpath(os.path.abspath(p)) != real


def server_clock_skew(server: str, opener=None, now: Callable[[], float] = time.time):
    """(skew_seconds, endpoint) or None. Server minus local time (Phase 17).

    Measured on `/api/health`, which is `Cache-Control: no-store`, so its `Date` is the origin's clock now. Falls back to
    `/api/config` (CDN-cached for a while). In both cases an `Age` header is added back: a cached response's `Date` is
    when the origin made it, `Date + Age` is the server time when the cache served it. Using a CDN copy's bare `Date`
    reported several minutes of false skew on synchronised machines. The local time is the request midpoint.
    """
    import urllib.request
    from email.utils import parsedate_to_datetime
    opener = opener or urllib.request.urlopen
    for ep in ("/api/health", "/api/config"):
        try:
            req = urllib.request.Request(server + ep, headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
            t0 = now()
            with opener(req, timeout=10) as r:
                date, age = r.headers.get("Date"), r.headers.get("Age")
            t1 = now()
            if not date:
                continue
            server_t = parsedate_to_datetime(date).timestamp()
        except Exception:
            continue
        try:
            a = int(str(age).strip()) if age is not None else 0
        except ValueError:
            a = 0
        return server_t + max(0, a) - (t0 + t1) / 2, ep
    return None


def cmd_doctor(args) -> int:
    home = _home(args)
    bad = 0
    OUT("Python: %s %s" % (sys.version.split()[0], "✓" if sys.version_info >= (3, 8) else "✗ (≥ 3.8 gerekli)"))
    OUT("Bağlayıcı: %s · klasör %s" % (__version__, home))
    mode = stat.S_IMODE(os.stat(home).st_mode)
    OUT("Klasör izinleri: %o %s" % (mode, "✓" if mode & 0o077 == 0 else "✗ (0700 olmalı)"))
    for name in (CFG.CONFIG_FILE, CFG.KEYS_FILE, CFG.PEERS_FILE, CFG.AGENTS_FILE, CFG.CONTROL_FILE):
        p = os.path.join(home, name)
        if os.path.exists(p):
            m = stat.S_IMODE(os.stat(p).st_mode)
            if m & 0o077:
                bad += 1
            OUT("  %-14s %o %s" % (name, m, "✓" if m & 0o077 == 0 else "✗ (0600 olmalı)"))
    cfg = CFG.load_config(home)
    if not cfg.ok:
        OUT("connector.json: ✗ (%s)" % "; ".join(cfg.errors[:3]))
        return 1
    OUT("connector.json: ✓")
    from .engines.claude import ClaudeEngine
    from .engines.ollama import OllamaEngine
    c = ClaudeEngine(cfg.data["engines"]["claude"], cfg.data["profiles"]).available()
    OUT("Claude Code: %s" % ("✓ %s (giriş: %s)" % (c["version"], c["auth"]) if c["available"] else "bulunamadı"))
    if c["available"] and cfg.data["workspaces"]:
        OUT("  Uyarı: Claude Code bu klasördeki proje ayarlarını çalıştırır (hook'lar, .mcp.json; güven sorusu yok).")
    o = OllamaEngine(cfg.data["engines"]["ollama"]).available()
    OUT("Ollama: %s" % ("✓ %s, %d model" % (o["version"], len(o["models"])) if o["available"] else "çalışmıyor"))
    if not cfg.data["workspaces"]:
        OUT("Çalışma klasörleri: ✗ yok — Claude Code ajanı oluşturulamaz. Düzeltmek için: "
            "pixel-connector workspace add ~/projem")
    else:
        OUT("Çalışma klasörleri: ✓ %d tane (pixel-connector workspace list)" % len(cfg.data["workspaces"]))
    try:
        import urllib.request
        with urllib.request.urlopen(cfg.data["server"] + "/api/config", timeout=10) as r:
            d = json.loads(r.read())
        relay = d.get("relay")
        OUT("Sunucu: ✓ %s · röle: %s" % (cfg.data["server"], relay["url"] if relay else "yapılandırılmamış ✗"))
        sk = server_clock_skew(cfg.data["server"])
        if sk is not None:
            skew = abs(sk[0])
            OUT("Saat farkı: %.0f sn %s" % (skew, "✓" if skew < 30 else "✗ (saat ayarını düzeltin)"))
    except Exception as e:
        bad += 1
        OUT("Sunucu: ✗ (%s)" % type(e).__name__)
    from . import service
    OUT("Servis: %s" % service.status())
    try:
        st, d = ctl_request(home, "GET", "/ctl/status", timeout=3)
        OUT("Çalışan bağlayıcı: röle %s, %d kanal, %d ajan" % (d["relay"], d["channels"], d["agents"]))
    except OSError:
        OUT("Çalışan bağlayıcı: yok")
    return 1 if bad else 0


def cmd_install_service(args) -> int:
    from . import service
    home = _home(args)
    pyz = args.pyz or service.default_pyz(home)
    if not args.dry_run and not os.path.exists(pyz):
        return _err("%s bulunamadı (kurulum betiği onu buraya kopyalar)." % pyz)
    rc, path = service.install(home, pyz, dry_run=args.dry_run)
    if rc == 0 and sys.platform.startswith("linux"):
        OUT("Servis kuruldu. Günlük: journalctl --user -u pixel-connector")
        OUT("İsteğe bağlı (oturum açmadan da çalışsın): loginctl enable-linger $USER")
    return rc


def cmd_uninstall_service(args) -> int:
    from . import service
    return service.uninstall(dry_run=args.dry_run)


def cmd_reset_keys(args) -> int:
    home = _home(args)
    if not args.yes and not ask_yes_no("Yeni cihaz anahtarı üretilecek; tüm tarayıcılar yeniden eşleşmeli. Devam? [e/H] "):
        OUT("Vazgeçildi.")
        return 1
    for name in (CFG.KEYS_FILE, CFG.PEERS_FILE):
        try:
            os.unlink(os.path.join(home, name))
        except FileNotFoundError:
            pass
    from .peers import DeviceKey
    k = DeviceKey.create(home)
    OUT("Yeni cihaz kimliği: %s. Bağlayıcıyı yeniden başlatın: systemctl --user restart pixel-connector" % k.device_id)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pixel-connector", description="Pixel Office bağlayıcı (v3)")
    p.add_argument("--home", help="bağlayıcı klasörü (varsayılan ~/.pixel-office/connector)")
    p.add_argument("--version", action="version", version="pixel-connector " + __version__)
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("setup")
    s.add_argument("--server", required=True)
    s.add_argument("--room", required=True)
    s.add_argument("--key", required=True)
    s.add_argument("--name", required=True)
    s.add_argument("--device-name")
    s.add_argument("--no-default-workspace", action="store_true",
                   help="~/PixelOfficeIsler varsayılan çalışma klasörünü oluşturma")
    sub.add_parser("run")
    sub.add_parser("pair")
    s = sub.add_parser("peers")
    s.add_argument("action", nargs="?", default="list", choices=["list", "revoke", "revoke-all"])
    s.add_argument("target", nargs="?")
    s = sub.add_parser("agents")
    s.add_argument("action", nargs="?", default="list", choices=["list", "delete"])
    s.add_argument("target", nargs="?")
    s = sub.add_parser("runtimes")
    s.add_argument("action", nargs="?", default="list", choices=["list", "start", "stop"])
    s.add_argument("target", nargs="?")
    s = sub.add_parser("usage")
    s.add_argument("--period", default="day", choices=["day", "week", "month"])
    s = sub.add_parser("approve")
    s.add_argument("id", nargs="?")
    s.add_argument("--link", action="store_true")
    s = sub.add_parser("deny")
    s.add_argument("id")
    s.set_defaults(link=False)
    s = sub.add_parser("workspace", help="çalışma klasörleri: list | add <yol> | remove <kimlik>")
    s.add_argument("action", nargs="?", default="list", choices=["list", "add", "remove"])
    s.add_argument("target", nargs="?", help="add: klasör yolu · remove: kimlik")
    s.add_argument("--name", help="görünen ad (varsayılan: klasör adı)")
    s.add_argument("--id", help="kimlik [a-z0-9_-]{1,24} (varsayılan: klasör adından)")
    s.add_argument("--children", action="store_true", help="alt klasörler seçilebilsin")
    sub.add_parser("doctor")
    sub.add_parser("config-check")
    s = sub.add_parser("install-service")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--pyz")
    s = sub.add_parser("uninstall-service")
    s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("reset-keys")
    s.add_argument("--yes", action="store_true")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd in ("peers",) and args.action == "revoke" and not args.target:
        return _err("kaldırılacak tarayıcının kimliğini (bid) yazın.")
    if args.cmd in ("agents",) and args.action == "delete" and not args.target:
        return _err("silinecek ajanın anahtarını yazın.")
    if args.cmd in ("runtimes",) and args.action in ("start", "stop") and not args.target:
        return _err("ortam kimliğini yazın.")
    if args.cmd == "workspace" and args.action in ("add", "remove") and not args.target:
        return _err("klasör yolunu yazın: pixel-connector workspace add ~/projem" if args.action == "add"
                    else "kaldırılacak klasörün kimliğini yazın (liste: pixel-connector workspace list).")
    table = {"setup": cmd_setup, "workspace": cmd_workspace, "run": cmd_run, "pair": cmd_pair, "peers": cmd_peers, "agents": cmd_agents,
             "runtimes": cmd_runtimes, "usage": cmd_usage, "approve": cmd_approve,
             "deny": lambda a: cmd_approve(a, ok=False), "doctor": cmd_doctor, "config-check": cmd_config_check,
             "install-service": cmd_install_service, "uninstall-service": cmd_uninstall_service,
             "reset-keys": cmd_reset_keys}
    fn = table.get(args.cmd)
    if fn is None:
        build_parser().print_help()
        return 0
    return fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
