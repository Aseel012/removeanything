"""
removeanything.in — Production Flask Backend
nadermx/backgroundremover for BG removal.
GargantuaX/allenk reverse-alpha-blending for Gemini Nano Banana watermark.
Google Sign-In only. API keys. Language support. Secure admin 2-step.
"""
import os, io, time, datetime, re, base64, hashlib, secrets, math
from pathlib import Path
from functools import wraps

# Load .env
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    with open(_env_path) as _ef:
        for _line in _ef:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

from flask import (Flask, request, jsonify, send_file,
                   render_template, session, redirect, abort)
from flask_cors import CORS
from PIL import Image, ImageFilter
import numpy as np

os.environ.setdefault("ORT_LOGGING_LEVEL", "3")
os.environ.setdefault("ONNXRUNTIME_PROVIDERS", "CPUExecutionProvider")
import warnings; warnings.filterwarnings("ignore")
import requests as _requests

# ── Try backgroundremover (nadermx), fallback to rembg ──────────
try:
    import backgroundremover.bg as _bgrm_bg
    def _do_remove_bg(img_bytes):
        """Use nadermx/backgroundremover — removes bg via u2net AI model."""
        from backgroundremover.bg import remove as _bgremove
        return _bgremove(img_bytes, model_name="u2net",
                         alpha_matting=False,
                         alpha_matting_foreground_threshold=240,
                         alpha_matting_background_threshold=10,
                         alpha_matting_erode_structure_size=10,
                         alpha_matting_base_size=1000)
    _BG_ENGINE = "backgroundremover"
except Exception:
    try:
        import rembg as _rembg_mod
        def _do_remove_bg(img_bytes):
            """Fallback: use rembg."""
            return _rembg_mod.remove(img_bytes)
        _BG_ENGINE = "rembg"
    except Exception:
        _do_remove_bg = None
        _BG_ENGINE = "none"

# ── App ──────────────────────────────────────────────────────────
app = Flask(__name__)
_raw_secret = os.environ.get("SECRET_KEY", "")
if not _raw_secret or len(_raw_secret) < 32:
    import sys; print("WARNING: SECRET_KEY missing — using ephemeral key", file=sys.stderr)
    _raw_secret = secrets.token_hex(48)

app.secret_key = _raw_secret
_https_mode = os.environ.get("HTTPS", "false").lower() == "true"
app.config.update(
    SESSION_COOKIE_HTTPONLY   = True,
    # "Lax" blocks cookies on cross-site POSTs triggered by Firebase OAuth.
    # Use "None" (+ Secure) in production HTTPS, "Lax" in HTTP dev.
    SESSION_COOKIE_SAMESITE   = "None" if _https_mode else "Lax",
    SESSION_COOKIE_SECURE     = _https_mode,
    SESSION_COOKIE_NAME       = "ra_session",
    PERMANENT_SESSION_LIFETIME= datetime.timedelta(hours=24),
    MAX_CONTENT_LENGTH        = 20 * 1024 * 1024,
    SEND_FILE_MAX_AGE_DEFAULT  = 0,
)
CORS(app,
     origins=os.environ.get("ALLOWED_ORIGINS",
                             "https://removeanything.in,http://localhost:5000,http://127.0.0.1:5000").split(","),
     supports_credentials=True,
     allow_headers=["Content-Type", "X-API-Key", "Authorization"],
     methods=["GET", "POST", "DELETE", "OPTIONS"])

# ── Constants ────────────────────────────────────────────────────
ALLOWED_IMAGE_TYPES = {"image/png","image/jpeg","image/webp","image/bmp","image/tiff"}
MAX_IMAGE_SIZE      = 20 * 1024 * 1024
FREE_LIMIT          = 3

FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "removeanything-53f79")
FIREBASE_WEB_CONFIG = {
    "apiKey":            os.environ.get("FIREBASE_API_KEY",            "AIzaSyCnrHDuP5vFJi5tVCecfQwIiSWVaxMAWto"),
    "authDomain":        os.environ.get("FIREBASE_AUTH_DOMAIN",        "removeanything-53f79.firebaseapp.com"),
    "projectId":         FIREBASE_PROJECT_ID,
    "storageBucket":     os.environ.get("FIREBASE_STORAGE_BUCKET",     "removeanything-53f79.firebasestorage.app"),
    "messagingSenderId": os.environ.get("FIREBASE_MESSAGING_SENDER_ID","1022112281671"),
    "appId":             os.environ.get("FIREBASE_APP_ID",             "1:1022112281671:web:f25d4304275a511085ad62"),
    "measurementId":     os.environ.get("FIREBASE_MEASUREMENT_ID",     "G-90X99WMSJY"),
}
ADMIN_EMAIL   = os.environ.get("ADMIN_EMAIL",    "shaikhmdaseel@gmail.com")
_raw_pw       = os.environ.get("ADMIN_PASSWORD", "12345678")
ADMIN_PW_HASH = hashlib.sha256(_raw_pw.encode()).hexdigest()

# ── Rate limiting ────────────────────────────────────────────────
_rate = {}; RATE_WINDOW = 60; RATE_LIMIT = 30

def _get_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    ip  = fwd.split(",")[0].strip() if fwd else (request.remote_addr or "")
    return re.sub(r"[^0-9a-fA-F.:]", "", ip)[:45] or "unknown"

def rate_check():
    ip = _get_ip(); now = time.time()
    hits = [t for t in _rate.get(ip, []) if now - t < RATE_WINDOW]
    if len(hits) >= RATE_LIMIT: abort(429)
    hits.append(now); _rate[ip] = hits
    if len(_rate) > 5000:
        cutoff = now - RATE_WINDOW
        for k in list(_rate.keys()):
            _rate[k] = [t for t in _rate[k] if t > cutoff]
            if not _rate[k]: del _rate[k]

# ── API key authentication (before_request) ───────────────────────
@app.before_request
def _inject_api_user():
    """
    If the request carries a valid X-API-Key header, inject the owner's
    uid/email into the session so all downstream auth checks pass.
    This runs before every request — zero changes needed in individual routes.
    """
    api_key = request.headers.get("X-API-Key", "").strip()
    if not api_key:
        return  # no API key header — proceed normally (session auth)
    # Look up the key
    for kid, rec in _api_keys.items():
        if rec.get("key") == api_key and rec.get("active", True):
            # Inject into session for this request cycle
            session["user_uid"]   = rec["uid"]
            session["user_email"] = rec.get("email", "")
            request._api_user     = rec.get("email", "")
            rec["uses"]           = rec.get("uses", 0) + 1
            rec["last_used"]      = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            return
    # Key provided but not valid — return 401 immediately
    abort(401)

# ── In-memory stores ─────────────────────────────────────────────
_stats = {"total_bg": 0, "total_nano": 0, "start_time": time.time(),
          "log": [], "users": {}, "hourly": {}, "daily": {}}
_ip_usage = {}

# API keys indexed by kid. Each record:
#   { "key": "ra_...", "uid": "...", "email": "...", "name": "...",
#     "created": "...", "uses": 0, "last_used": "—", "active": True }
_api_keys = {}

# ── Helpers ──────────────────────────────────────────────────────
def _gen_api_key(): return "ra_" + secrets.token_urlsafe(32)

def _mask_ip(ip):
    parts = ip.split(".")
    return f"{parts[0]}.{parts[1]}.*.*" if len(parts) == 4 else ip[:8] + "…"

def _make_thumb(img):
    try:
        t = img.copy(); t.thumbnail((96, 96), Image.LANCZOS); buf = io.BytesIO()
        if t.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", t.size, (255, 255, 255))
            bg.paste(t, mask=t.split()[3 if t.mode == "RGBA" else 1])
            bg.save(buf, format="JPEG", quality=70)
        else:
            t.convert("RGB").save(buf, format="JPEG", quality=70)
        return base64.b64encode(buf.getvalue()).decode()
    except: return ""

def _log(action, filename, size_in, size_out, img_data=None):
    safe = re.sub(r"[^\w.\-]", "_", (filename or "unknown"))[:80]
    user_email = getattr(request, "_api_user", None) or session.get("user_email", "")
    now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    now_dt  = datetime.datetime.utcnow()
    entry = {"id": secrets.token_hex(4), "action": action, "filename": safe,
             "size_in": int(size_in), "size_out": int(size_out), "ts": now_str,
             "img_data": img_data, "ip": _mask_ip(_get_ip()), "email": user_email}
    _stats["log"].insert(0, entry); _stats["log"] = _stats["log"][:500]
    hk = now_dt.strftime("%Y-%m-%d %H"); dk = now_dt.strftime("%Y-%m-%d")
    _stats["hourly"].setdefault(hk, {"bg": 0, "nano": 0})
    _stats["daily"].setdefault(dk,  {"bg": 0, "nano": 0})
    field = "bg" if action == "bg_remove" else "nano"
    _stats["hourly"][hk][field] += 1; _stats["daily"][dk][field] += 1
    if len(_stats["hourly"]) > 200: del _stats["hourly"][sorted(_stats["hourly"])[0]]
    if len(_stats["daily"])  > 100: del _stats["daily"][sorted(_stats["daily"])[0]]
    if user_email:
        _stats["users"].setdefault(user_email, {"count": 0, "bg": 0, "nano": 0,
                                                 "first_seen": now_str, "last_seen": now_str})
        _stats["users"][user_email]["count"] += 1
        _stats["users"][user_email][field] = _stats["users"][user_email].get(field, 0) + 1
        _stats["users"][user_email]["last_seen"] = now_str

def require_admin(f):
    @wraps(f)
    def dec(*a, **kw):
        if not session.get("admin"): return redirect("/admin")
        return f(*a, **kw)
    return dec

# ── Security headers ─────────────────────────────────────────────
@app.after_request
def security_headers(resp):
    resp.headers["X-Frame-Options"]        = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
    resp.headers["X-XSS-Protection"]       = "0"
    resp.headers["Permissions-Policy"]     = "camera=(), microphone=(), geolocation=(), payment=()"
    fb = os.environ.get("FIREBASE_AUTH_DOMAIN", "removeanything-53f79.firebaseapp.com")
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
        "https://www.gstatic.com https://apis.google.com https://www.googleapis.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' data: blob:; "
        "connect-src 'self' https://identitytoolkit.googleapis.com "
        "https://securetoken.googleapis.com https://www.googleapis.com "
        f"https://firestore.googleapis.com https://{FIREBASE_PROJECT_ID}.firebaseio.com; "
        f"frame-src https://{fb} https://accounts.google.com;"
    )
    if request.path.startswith(("/admin", "/auth", "/remove-", "/api/")):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        resp.headers["Pragma"]        = "no-cache"
    resp.headers.pop("Server", None); resp.headers.pop("X-Powered-By", None)
    return resp

# ── Image validation ─────────────────────────────────────────────
def validate_image(file):
    if not file or not getattr(file, "filename", ""): return None, "No file provided"
    content = file.read()
    if len(content) > MAX_IMAGE_SIZE: return None, "File too large (max 20 MB)"
    ct = (file.content_type or "").lower().split(";")[0].strip()
    if ct not in ALLOWED_IMAGE_TYPES: return None, f"Unsupported file type: {ct}"
    magic = content[:12]
    valid = (magic[:8] == b"\x89PNG\r\n\x1a\n" or
             magic[:3] == b"\xff\xd8\xff" or
             (magic[:4] == b"RIFF" and magic[8:12] == b"WEBP") or
             magic[:2] == b"BM" or
             magic[:4] in (b"II*\x00", b"MM\x00*"))
    if not valid: return None, "File content does not match declared type"
    try: img = Image.open(io.BytesIO(content)); img.verify()
    except: return None, "Invalid or corrupt image file"
    return content, None

# ══════════════════════════════════════════════════════════════════
# GEMINI NANO BANANA — py-gemini-watermark-remover package
# pip install py-gemini-watermark-remover
# Uses real captured alpha maps from actual Gemini watermark images.
# Formula: watermarked = α × logo(255) + (1-α) × original
# Reverse: original    = (watermarked - α×255) / (1-α)
# ══════════════════════════════════════════════════════════════════
try:
    import cv2 as _cv2
    from gemini_watermark_remover import WatermarkRemover as _WMRemover
    _wm_engine = _WMRemover(logo_value=255.0)
    _WM_ENGINE = "py-gemini-watermark-remover"
except Exception as _wm_err:
    _wm_engine = None
    _cv2       = None
    _WM_ENGINE = "fallback"


def _pil_to_bgr(img_pil):
    """Convert PIL Image → OpenCV BGR ndarray (no cropping)."""
    rgb = np.array(img_pil.convert("RGB"), dtype=np.uint8)
    return _cv2.cvtColor(rgb, _cv2.COLOR_RGB2BGR)

def _bgr_to_pil(arr_bgr):
    """Convert OpenCV BGR ndarray → PIL Image."""
    rgb = _cv2.cvtColor(arr_bgr, _cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)

def remove_gemini_watermark(img_pil):
    """
    Remove Gemini Nano Banana sparkle watermark.

    Primary  : py-gemini-watermark-remover — real captured alpha maps,
               auto size: 48×48 for images ≤1024px, 96×96 for images
               where BOTH dimensions > 1024px.
               auto_detect=False so every upload is always processed.
    Fallback : built-in reverse-alpha-blending using wm_48/wm_96 PNGs.

    The full image is always returned at original resolution — zero cropping.
    """
    if _wm_engine is not None and _cv2 is not None:
        bgr         = _pil_to_bgr(img_pil)
        cleaned_bgr = _wm_engine.remove_watermark(bgr, auto_detect=False)
        return _bgr_to_pil(cleaned_bgr)

    # ── Fallback: built-in reverse alpha blending ──────────────────
    img_rgb = img_pil.convert("RGB")
    arr     = np.array(img_rgb, dtype=np.float32)
    h, w    = arr.shape[:2]

    # Match py-gemini-watermark-remover's exact sizing rule:
    # BOTH width AND height > 1024  →  large (96×96, 64px margin)
    if w > 1024 and h > 1024:
        wm_size, margin = 96, 64
    else:
        wm_size, margin = 48, 32

    # Load alpha map from bundled PNGs
    wm_png = Path(__file__).parent / "static" / "img" / f"wm_{wm_size}.png"
    try:
        raw = Image.open(wm_png)
        if raw.mode == "RGBA":
            alpha_map = np.array(raw, dtype=np.float32)[:, :, 3] / 255.0
        else:
            alpha_map = np.array(raw.convert("L"), dtype=np.float32) / 255.0
        if alpha_map.shape[0] != wm_size or alpha_map.shape[1] != wm_size:
            r2 = Image.fromarray((alpha_map * 255).astype(np.uint8), "L")
            alpha_map = np.array(r2.resize((wm_size, wm_size), Image.LANCZOS), dtype=np.float32) / 255.0
    except Exception:
        alpha_map = _fallback_star_alpha(wm_size)

    x1 = max(0, w - wm_size - margin)
    y1 = max(0, h - wm_size - margin)
    x2, y2 = min(w, x1 + wm_size), min(h, y1 + wm_size)
    rw, rh  = x2 - x1, y2 - y1

    alpha_region = alpha_map[:rh, :rw]
    region       = arr[y1:y2, x1:x2].copy()
    denom        = np.maximum(1.0 - alpha_region, 0.01)
    for c in range(3):
        region[:, :, c] = np.clip(
            (region[:, :, c] - alpha_region * 255.0) / denom, 0.0, 255.0
        )
    arr[y1:y2, x1:x2] = region
    return Image.fromarray(arr.astype(np.uint8), "RGB")


def _fallback_star_alpha(size):
    """Analytical 4-pointed Gemini sparkle — used only when PNG assets are missing."""
    import math as _math
    alpha = np.zeros((size, size), dtype=np.float32)
    cx = cy = size / 2.0
    outer_h, outer_d = size * 0.47, size * 0.12
    for y in range(size):
        for x in range(size):
            dx, dy = float(x) - cx, float(y) - cy
            dist = _math.sqrt(dx * dx + dy * dy)
            if dist < 0.5:
                alpha[y, x] = 0.86; continue
            sector = _math.atan2(dy, dx) % (_math.pi / 2)
            t      = abs(sector - _math.pi / 4) / (_math.pi / 4)
            star_r = outer_d + (outer_h - outer_d) * t
            edge_w = max(1.5, size * 0.03)
            if   dist < star_r - edge_w: alpha[y, x] = 0.86
            elif dist < star_r + edge_w: alpha[y, x] = 0.86 * max(0.0, (star_r + edge_w - dist) / (2 * edge_w))
    from PIL import ImageFilter as _IF
    img = Image.fromarray((alpha * 255).astype(np.uint8), "L")
    img = img.filter(_IF.GaussianBlur(radius=max(1, size * 0.025)))
    return np.array(img, dtype=np.float32) / 255.0

# ── Firebase token verification ───────────────────────────────────
_firebase_keys_cache = {"keys": {}, "expires": 0}

def _get_firebase_public_keys():
    now = time.time()
    if now < _firebase_keys_cache["expires"] and _firebase_keys_cache["keys"]:
        return _firebase_keys_cache["keys"]
    try:
        r = _requests.get(
            "https://www.googleapis.com/robot/v1/metadata/x509/"
            "securetoken@system.gserviceaccount.com", timeout=5)
        ma = 3600
        m = re.search(r"max-age=(\d+)", r.headers.get("Cache-Control", ""))
        if m: ma = int(m.group(1))
        _firebase_keys_cache["keys"]    = r.json()
        _firebase_keys_cache["expires"] = now + ma
    except: pass
    return _firebase_keys_cache["keys"]

def _verify_firebase_token(id_token):
    """
    Verify a Firebase ID token using PyJWT 2.x + cryptography.
    Google's public key endpoint returns PEM-encoded X.509 certificates.
    PyJWT 2.x cannot use raw X.509 PEM strings — we must extract the
    RSA public key from the certificate first using the cryptography library.
    Falls back to Google's identitytoolkit REST lookup if PyJWT is absent.
    """
    try:
        import jwt as pyjwt
        keys_pem = _get_firebase_public_keys()
        header   = pyjwt.get_unverified_header(id_token)
        kid      = header.get("kid", "")
        if kid not in keys_pem:
            raise ValueError(f"Unknown key ID: {kid!r} (have {list(keys_pem.keys())[:3]})")

        # PyJWT 2.x: extract RSA public key from X.509 certificate PEM
        pem = keys_pem[kid]
        try:
            from cryptography.x509 import load_pem_x509_certificate
            from cryptography.hazmat.backends import default_backend
            cert     = load_pem_x509_certificate(pem.encode(), default_backend())
            pub_key  = cert.public_key()
        except Exception as cert_err:
            # If it's already a raw public key PEM (not a cert), use it directly
            app.logger.warning("Cert parse failed (%s), trying raw PEM", cert_err)
            pub_key = pem  # PyJWT can handle raw RSA public key PEM strings

        return pyjwt.decode(
            id_token, pub_key, algorithms=["RS256"],
            audience=FIREBASE_PROJECT_ID,
            issuer=f"https://securetoken.google.com/{FIREBASE_PROJECT_ID}"
        )
    except ImportError:
        # PyJWT not installed — use Google's identitytoolkit REST lookup
        api_key = os.environ.get("FIREBASE_API_KEY", "")
        if not api_key:
            raise ValueError("FIREBASE_API_KEY not set and PyJWT not installed")
        try:
            r = _requests.post(
                f"https://identitytoolkit.googleapis.com/v1/accounts:lookup?key={api_key}",
                json={"idToken": id_token}, timeout=8
            )
        except Exception as net_err:
            raise ValueError(f"Token lookup network error: {net_err}")
        if r.status_code != 200:
            raise ValueError(f"Token lookup failed ({r.status_code}): {r.text[:200]}")
        users = r.json().get("users", [])
        if not users:
            raise ValueError("Token lookup returned no user")
        return users[0]
    except Exception as e:
        app.logger.error("Firebase token verification error: %s: %s", type(e).__name__, str(e)[:200])
        raise ValueError(str(e))

# ══════════════════════════════════════════════════════════════════
# ROUTES — AUTH
# ══════════════════════════════════════════════════════════════════

def _verify_via_identitytoolkit(id_token):
    """Secondary verification via Google identitytoolkit REST API (no local crypto needed)."""
    api_key = os.environ.get("FIREBASE_API_KEY", "AIzaSyCnrHDuP5vFJi5tVCecfQwIiSWVaxMAWto")
    r = _requests.post(
        f"https://identitytoolkit.googleapis.com/v1/accounts:lookup?key={api_key}",
        json={"idToken": id_token}, timeout=8
    )
    if r.status_code != 200:
        raise ValueError(f"identitytoolkit lookup failed ({r.status_code})")
    users = r.json().get("users", [])
    if not users:
        raise ValueError("No user found in identitytoolkit response")
    u = users[0]
    return {"sub": u.get("localId", ""), "uid": u.get("localId", ""),
            "email": u.get("email", ""), "localId": u.get("localId", "")}


@app.route("/auth/verify", methods=["POST"])
def auth_verify():
    data     = request.get_json(silent=True) or {}
    id_token = str(data.get("idToken", "")).strip()
    if not id_token or len(id_token) > 4096:
        return jsonify({"error": "Missing or invalid token"}), 400
    try:
        payload = _verify_firebase_token(id_token)
    except Exception as e:
        # Primary local-crypto verify failed — try Google REST fallback
        app.logger.warning("Primary verify failed (%s), trying REST fallback", str(e)[:100])
        try:
            payload = _verify_via_identitytoolkit(id_token)
        except Exception as e2:
            app.logger.warning("Firebase token verify failed from %s: %s", _get_ip(), str(e2)[:200])
            return jsonify({"error": "Authentication failed"}), 401

    uid   = str(payload.get("uid") or payload.get("localId") or payload.get("sub") or "")
    email = str(payload.get("email") or "")
    if not uid:
        return jsonify({"error": "Invalid token payload — no uid"}), 401

    # Persist uid + email in server-side session
    session.clear()                        # clear any stale data first
    session["user_uid"]   = uid
    session["user_email"] = email
    session.permanent     = True           # respect PERMANENT_SESSION_LIFETIME
    session.modified      = True

    now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    _stats["users"].setdefault(email, {"count": 0, "bg": 0, "nano": 0,
                                       "first_seen": now_str, "last_seen": now_str})
    if email:
        _stats["users"][email]["last_seen"] = now_str

    is_admin = email.lower() == ADMIN_EMAIL.lower()
    if is_admin:
        session["google_authed_admin"] = True
        session.pop("admin", None)
        session.modified = True

    app.logger.info("Auth OK uid=%s email=%s admin=%s", uid[:8], email, is_admin)
    return jsonify({"ok": True, "uid": uid, "email": email,
                    "needs_admin_password": is_admin})

@app.route("/auth/me")
def auth_me():
    uid = session.get("user_uid")
    if uid:
        return jsonify({"logged_in": True, "uid": uid,
                        "email": session.get("user_email", "")})
    return jsonify({"logged_in": False}), 401

@app.route("/auth/logout", methods=["GET", "POST"])
def auth_logout():
    session.clear()
    return jsonify({"ok": True})

# ══════════════════════════════════════════════════════════════════
# ROUTES — USAGE
# ══════════════════════════════════════════════════════════════════

@app.route("/usage/check", methods=["POST"])
def usage_check():
    if session.get("user_uid"):
        return jsonify({"allowed": True, "logged_in": True, "remaining": -1})
    ip   = _get_ip(); used = _ip_usage.get(ip, 0)
    return jsonify({"allowed": used < FREE_LIMIT, "logged_in": False,
                    "remaining": max(0, FREE_LIMIT - used), "used": used})

@app.route("/usage/bump", methods=["POST"])
def usage_bump():
    if session.get("user_uid"): return jsonify({"ok": True})
    ip = _get_ip(); _ip_usage[ip] = _ip_usage.get(ip, 0) + 1
    if len(_ip_usage) > 10000:
        for k in list(_ip_usage.keys())[:5000]: del _ip_usage[k]
    return jsonify({"ok": True, "used": _ip_usage[ip]})

@app.route("/health")
def health():
    return jsonify({"status": "ok", "bg_engine": _BG_ENGINE, "wm_engine": _WM_ENGINE,
                    "time": datetime.datetime.utcnow().isoformat()})

# ══════════════════════════════════════════════════════════════════
# ROUTES — PAGES
# ══════════════════════════════════════════════════════════════════

@app.route("/")
def index(): return render_template("index.html", firebase_config=FIREBASE_WEB_CONFIG)

@app.route("/bg-remover")
def page_bg(): return render_template("bg_remover.html", firebase_config=FIREBASE_WEB_CONFIG)

@app.route("/nano-banana")
def page_nano(): return render_template("nano_banana.html", firebase_config=FIREBASE_WEB_CONFIG)

# ══════════════════════════════════════════════════════════════════
# ROUTES — PROCESSING
# ══════════════════════════════════════════════════════════════════

@app.route("/remove-bg", methods=["POST"])
def remove_bg():
    rate_check()
    if _do_remove_bg is None:
        return jsonify({"error": "Background removal engine not installed."}), 503
    file = request.files.get("image")
    content, err = validate_image(file)
    if err: return jsonify({"error": err}), 400
    mode = request.form.get("mode", "transparent")
    if mode not in ("transparent", "white", "black"): mode = "transparent"
    try:
        # nadermx/backgroundremover works on raw bytes, returns RGBA bytes
        result_bytes = _do_remove_bg(content)
        result_rgba  = Image.open(io.BytesIO(result_bytes)).convert("RGBA")

        if mode == "white":
            bg = Image.new("RGBA", result_rgba.size, (255, 255, 255, 255))
            bg.paste(result_rgba, mask=result_rgba.split()[3])
            out_img = bg.convert("RGB")
        elif mode == "black":
            bg = Image.new("RGBA", result_rgba.size, (0, 0, 0, 255))
            bg.paste(result_rgba, mask=result_rgba.split()[3])
            out_img = bg.convert("RGB")
        else:
            out_img = result_rgba

        buf = io.BytesIO()
        out_img.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        _stats["total_bg"] += 1
        thumb_img = out_img.convert("RGB") if out_img.mode == "RGBA" else out_img
        _log("bg_remove", file.filename, len(content), buf.getbuffer().nbytes,
             _make_thumb(thumb_img))
        resp = send_file(buf, mimetype="image/png", as_attachment=False,
                         download_name="result_nobg.png")
        resp.headers["Cache-Control"] = "no-store, private"
        return resp
    except MemoryError:
        return jsonify({"error": "Image too large to process."}), 413
    except Exception as e:
        app.logger.error("remove-bg error: %s — %s", type(e).__name__, str(e)[:80])
        return jsonify({"error": "Processing failed. Please try again."}), 500


@app.route("/remove-nano-banana", methods=["POST"])
def remove_nano_banana():
    rate_check()
    file = request.files.get("image")
    content, err = validate_image(file)
    if err: return jsonify({"error": err}), 400
    try:
        img    = Image.open(io.BytesIO(content))
        result = remove_gemini_watermark(img)
        buf    = io.BytesIO()
        result.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        _stats["total_nano"] += 1
        _log("nano_banana", file.filename, len(content), buf.getbuffer().nbytes,
             _make_thumb(result))
        resp = send_file(buf, mimetype="image/png", as_attachment=False,
                         download_name="result_clean.png")
        resp.headers["Cache-Control"] = "no-store, private"
        return resp
    except MemoryError:
        return jsonify({"error": "Image too large."}), 413
    except Exception as e:
        app.logger.error("nano-banana error: %s — %s", type(e).__name__, str(e)[:80])
        return jsonify({"error": "Processing failed."}), 500

# ══════════════════════════════════════════════════════════════════
# ROUTES — API KEYS
# Keyed by uid so email changes don't break lookups.
# ══════════════════════════════════════════════════════════════════

def _keys_for_uid(uid):
    return {kid: r for kid, r in _api_keys.items() if r.get("uid") == uid}

@app.route("/api/keys", methods=["GET"])
def api_keys_list():
    uid = session.get("user_uid")
    if not uid: return jsonify({"error": "Not logged in"}), 401
    keys = []
    for kid, r in _api_keys.items():
        if r.get("uid") == uid:
            keys.append({
                "id":          kid,
                "name":        r.get("name", ""),
                "key_preview": r["key"][:14] + "…",
                "created":     r.get("created", ""),
                "uses":        r.get("uses", 0),
                "last_used":   r.get("last_used", "—"),
                "active":      r.get("active", True),
            })
    return jsonify({"keys": keys})

@app.route("/api/keys", methods=["POST"])
def api_keys_create():
    uid   = session.get("user_uid")
    email = session.get("user_email", "")
    if not uid: return jsonify({"error": "Not logged in"}), 401
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "API Key"))[:60]
    if len(_keys_for_uid(uid)) >= 10:
        return jsonify({"error": "Maximum 10 API keys per account"}), 400
    kid = secrets.token_hex(8)
    key = _gen_api_key()
    _api_keys[kid] = {
        "key":     key,
        "uid":     uid,
        "email":   email,
        "name":    name,
        "created": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "uses":    0,
        "last_used": "—",
        "active":  True,
    }
    return jsonify({"id": kid, "key": key, "name": name})

@app.route("/api/keys/<kid>", methods=["DELETE"])
def api_keys_delete(kid):
    uid = session.get("user_uid")
    if not uid: return jsonify({"error": "Not logged in"}), 401
    if not re.fullmatch(r"[0-9a-f]{16}", kid): abort(400)
    rec = _api_keys.get(kid)
    if not rec or rec.get("uid") != uid:
        return jsonify({"error": "Not found"}), 404
    del _api_keys[kid]
    return jsonify({"ok": True})

@app.route("/api/keys/<kid>/toggle", methods=["POST"])
def api_keys_toggle(kid):
    uid = session.get("user_uid")
    if not uid: return jsonify({"error": "Not logged in"}), 401
    if not re.fullmatch(r"[0-9a-f]{16}", kid): abort(400)
    rec = _api_keys.get(kid)
    if not rec or rec.get("uid") != uid:
        return jsonify({"error": "Not found"}), 404
    rec["active"] = not rec.get("active", True)
    return jsonify({"ok": True, "active": rec["active"]})

# ══════════════════════════════════════════════════════════════════
# ROUTES — ADMIN
# 2-step: (1) Google sign-in verified server-side, (2) password
# ══════════════════════════════════════════════════════════════════

@app.route("/admin", methods=["GET"])
def admin_login_page():
    if session.get("admin"):
        return redirect("/admin/dashboard")
    # Step 2: Google already verified, show password form
    if session.get("google_authed_admin"):
        return render_template("admin.html", view="password_step",
                               email=ADMIN_EMAIL, error=None,
                               firebase_config=FIREBASE_WEB_CONFIG)
    # Step 1: show Google sign-in button
    return render_template("admin.html", view="login", error=None,
                           firebase_config=FIREBASE_WEB_CONFIG)

@app.route("/admin/login", methods=["POST"])
def admin_login():
    # CSRF-lite: check Referer contains our host
    host    = request.headers.get("Host", "")
    referer = request.headers.get("Referer", "")
    if host and referer and host not in referer: abort(403)

    if not session.get("google_authed_admin"):
        return render_template("admin.html", view="login",
                               error="Please sign in with Google first.",
                               firebase_config=FIREBASE_WEB_CONFIG)

    email    = (request.form.get("email", "") or "").strip().lower()
    password =  request.form.get("password", "") or ""
    email_ok = secrets.compare_digest(email, ADMIN_EMAIL.lower())
    pw_hash  = hashlib.sha256(password.encode()).hexdigest()
    pw_ok    = secrets.compare_digest(pw_hash, ADMIN_PW_HASH)
    time.sleep(0.4 + secrets.randbelow(400) / 1000)  # timing-safe delay

    if email_ok and pw_ok:
        session["admin"]    = True
        session["admin_at"] = time.time()
        session.pop("google_authed_admin", None)
        session.permanent = True
        session.modified  = True
        return redirect("/admin/dashboard")

    app.logger.warning("Failed admin login from %s", _get_ip())
    return render_template("admin.html", view="password_step",
                           email=ADMIN_EMAIL, error="Wrong password.",
                           firebase_config=FIREBASE_WEB_CONFIG)

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin")

@app.route("/admin/dashboard")
@require_admin
def admin_dashboard():
    return render_template("admin.html", view="dashboard",
                           firebase_config=FIREBASE_WEB_CONFIG)

@app.route("/admin/stats")
@require_admin
def admin_stats():
    uptime_s = int(time.time() - _stats["start_time"])
    h, rem   = divmod(uptime_s, 3600)
    m, s     = divmod(rem, 60)
    safe_log = [{k: v for k, v in e.items() if k != "img_data"}
                for e in _stats["log"][:100]]
    users_list = [{"email": e, **i} for e, i in _stats["users"].items()]
    users_list.sort(key=lambda x: x.get("last_seen", ""), reverse=True)
    now_dt = datetime.datetime.utcnow()

    hourly_chart = []
    for i in range(23, -1, -1):
        dt = now_dt - datetime.timedelta(hours=i)
        hk = dt.strftime("%Y-%m-%d %H")
        bk = _stats["hourly"].get(hk, {"bg": 0, "nano": 0})
        hourly_chart.append({"label": dt.strftime("%H:00"),
                              "bg": bk["bg"], "nano": bk["nano"]})

    daily_chart = []
    for i in range(29, -1, -1):
        dt = now_dt - datetime.timedelta(days=i)
        dk = dt.strftime("%Y-%m-%d")
        bk = _stats["daily"].get(dk, {"bg": 0, "nano": 0})
        daily_chart.append({"label": dt.strftime("%m/%d"),
                             "bg": bk["bg"], "nano": bk["nano"]})

    api_stats = sorted(
        [{"id": kid, "email": r.get("email",""), "name": r.get("name",""),
          "uses": r.get("uses",0), "active": r.get("active",True),
          "created": r.get("created",""), "last_used": r.get("last_used","—")}
         for kid, r in _api_keys.items()],
        key=lambda x: x["uses"], reverse=True)

    return jsonify({
        "total_bg":   _stats["total_bg"],
        "total_nano": _stats["total_nano"],
        "total":      _stats["total_bg"] + _stats["total_nano"],
        "uptime":     f"{h}h {m}m {s}s",
        "log":        safe_log,
        "server_time": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "users":       users_list[:200],
        "active_ips":  len(_ip_usage),
        "hourly_chart": hourly_chart,
        "daily_chart":  daily_chart,
        "api_keys":     api_stats,
        "total_api_keys": len(_api_keys),
        "bg_engine":    _BG_ENGINE,
    })

@app.route("/admin/image-preview/<log_id>")
@require_admin
def admin_image_preview(log_id):
    if not re.fullmatch(r"[0-9a-f]{8}", log_id): abort(400)
    for entry in _stats["log"]:
        if entry["id"] == log_id and entry.get("img_data"):
            resp = send_file(io.BytesIO(base64.b64decode(entry["img_data"])),
                             mimetype="image/jpeg")
            resp.headers["Cache-Control"] = "private, max-age=300"
            return resp
    abort(404)

# ── Error handlers ────────────────────────────────────────────────
@app.errorhandler(400)
def bad_request(e):     return jsonify({"error": "Bad request"}), 400
@app.errorhandler(403)
def forbidden(e):       return jsonify({"error": "Forbidden"}), 403
@app.errorhandler(404)
def not_found(e):       return jsonify({"error": "Not found"}), 404
@app.errorhandler(405)
def method_not_allowed(e): return jsonify({"error": "Method not allowed"}), 405
@app.errorhandler(413)
def too_large(e):       return jsonify({"error": "File too large"}), 413
@app.errorhandler(429)
def too_many(e):        return jsonify({"error": "Rate limit — wait a moment."}), 429
@app.errorhandler(500)
def server_error(e):    return jsonify({"error": "Something went wrong"}), 500
@app.errorhandler(Exception)
def unhandled(e):
    app.logger.error("Unhandled: %s — %s", type(e).__name__, str(e)[:100])
    return jsonify({"error": "Something went wrong"}), 500

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 7860))
    app.run(host="0.0.0.0", port=port)
