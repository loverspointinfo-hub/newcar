#!/usr/bin/env python3
"""
UIDAI MyAadhaar Telegram Bot
- /verify (basic) working
- full login: sticky session + otp txnId + retry on UOS-AUT-001
"""

import os
import re
import json
import base64
import hashlib
import secrets
import logging
import uuid
import time
import requests
from io import BytesIO

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("aadhaar-bot")

BOT_TOKEN = os.environ.get(
    "BOT_TOKEN", "9115703725:AAG_hwSNAlKMBcT1YmfibdSzv7JNgeknflk"
)

BASE = "https://tathya.uidai.gov.in"
ORIGIN = "https://myaadhaar.uidai.gov.in"
PROFILE_URL = f"{BASE}/profileService/api/v1/oauth/profile"
DOWNLOAD_URL = f"{BASE}/downloadAadhaarService/api/ext/oauth/aadhaar/download"
BANK_URL = f"{BASE}/statusCheckService/api/oauth/npci/aadhaar-seeding-status"
BIO_URL = f"{BASE}/lockUnlockBiometrics/ext/v1/oauth/getUidPreferences"
HOF_URL = f"{BASE}/ssupService/api/demographics/request/v4/fetch/hof"
VERIFY_URL = f"{BASE}/uidVerifyRetrieveService/api/verifyUID"
CAPTCHA_V3_URL = f"{BASE}/audioCaptchaService/api/captcha/v3/generation"

PROXY_URL = (
    "socks5h://834hJVszdL50_custom_zone_IN_st__city_sid_08454572_time_90:5304656"
    "@change5.owlproxy.com:7778"
)
PROXIES = {"http": PROXY_URL, "https": PROXY_URL}

UA = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36"
)

AADHAAR, CAPTCHA, OTP, MENU, VERIFY_CAPTCHA = range(5)

MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["1️⃣ Profile + Photo", "2️⃣ Aadhaar PDF"],
        ["3️⃣ Bank Seeding", "4️⃣ Bio-Lock Status"],
        ["5️⃣ Head of Family", "6️⃣ Basic Verify"],
        ["🔄 New Aadhaar", "❌ Close"],
    ],
    resize_keyboard=True,
)


def make_pkce():
    verifier = secrets.token_urlsafe(96)[:128]
    hex_digest = hashlib.sha256(verifier.encode()).hexdigest()
    challenge = base64.b64encode(hex_digest.encode()).decode()
    return verifier, challenge


def new_session(code_challenge: str, telemetry_id: str):
    """Fresh cookies every time — no leftover SESSION."""
    s = requests.Session()
    s.proxies.update(PROXIES)
    s.headers.update(
        {
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-IN,en-GB;q=0.9,en;q=0.8,hi;q=0.7",
            "sec-ch-ua": '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
        }
    )
    s.cookies.clear()
    try:
        r = s.get(
            f"{BASE}/access/oauth/authorize",
            params={
                "grant_type": "authorization_code",
                "response_type": "code",
                "client_id": "myAadhaar",
                "scope": "READ",
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "telemetry_session_id": telemetry_id,
            },
            headers={
                "Referer": ORIGIN + "/",
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            },
            allow_redirects=True,
            timeout=40,
        )
        log.info("authorize warm status=%s cookies=%s", r.status_code, dict(s.cookies))
    except Exception as e:
        log.warning("authorize warm: %s", e)
    try:
        s.get(
            f"{BASE}/access/login?role=resident",
            headers={"Referer": ORIGIN + "/"},
            timeout=30,
        )
    except Exception as e:
        log.warning("login page: %s", e)
    return s


def rid():
    return str(uuid.uuid4())


def generate_captcha(session: requests.Session):
    r = session.get(
        f"{BASE}/access/generateCaptcha",
        headers={"X-Request-Id": rid(), "Accept": "application/json, text/plain, */*"},
        timeout=40,
    )
    log.info("captcha HTTP %s body[:180]=%s", r.status_code, (r.text or "")[:180])
    if r.status_code != 200:
        raise RuntimeError(f"captcha HTTP {r.status_code}")
    data = r.json()
    msg = data.get("message")
    if msg is None:
        msg = data
    if isinstance(msg, str):
        msg = json.loads(msg)
    img_b64 = msg.get("imageBase64") or ""
    txn = msg.get("transactionId") or ""
    if not img_b64 or not txn:
        raise RuntimeError(f"captcha missing: {list(msg.keys()) if isinstance(msg, dict) else msg}")
    if img_b64.strip().startswith("data:"):
        img_b64 = img_b64.split(",", 1)[-1]
    return base64.b64decode(img_b64), txn


def send_otp(session, uid, captcha, captcha_txn):
    r = session.post(
        f"{BASE}/access/generateOTPForOAuth",
        headers={
            "X-Request-Id": rid(),
            "Content-Type": "application/json",
            "Origin": "https://tathya.uidai.gov.in",
            "Referer": "https://tathya.uidai.gov.in/access/login?role=resident",
        },
        json={"uid": uid, "captcha": captcha, "captchaTxnId": captcha_txn},
        timeout=40,
    )
    return r.status_code, r.json() if r.content else {}


def login_otp(session, uid, otp, otp_txn=None):
    """HAR body is only uid+otp. Safe-parse empty/non-JSON responses."""
    body = {"uid": str(uid), "otp": str(otp)}
    r = session.post(
        f"{BASE}/access/login",
        headers={
            "X-Request-Id": rid(),
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://tathya.uidai.gov.in",
            "Referer": "https://tathya.uidai.gov.in/access/login?role=resident",
        },
        json=body,
        timeout=40,
    )
    log.info("login raw status=%s len=%s head=%s", r.status_code, len(r.content or b""), (r.text or "")[:200])
    if not r.content:
        return r.status_code, {"status": "N", "errorMessage": f"empty response HTTP {r.status_code}"}
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"status": "N", "errorMessage": f"non-json HTTP {r.status_code}: {(r.text or '')[:200]}"}


def fetch_code(session, code_challenge, telemetry_id):
    r = session.get(
        f"{BASE}/access/oauth/authorize",
        params={
            "grant_type": "authorization_code",
            "response_type": "code",
            "client_id": "myAadhaar",
            "scope": "READ",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "telemetry_session_id": telemetry_id,
        },
        headers={
            "Referer": "https://tathya.uidai.gov.in/access/login?role=resident",
            "Accept": "text/html,*/*",
        },
        allow_redirects=True,
        timeout=40,
    )
    for hist in list(r.history) + [r]:
        m = re.search(r"[?&]code=([^&\s]+)", hist.url)
        if m:
            return m.group(1)
    m = re.search(r"[?&]code=([^&\s\"']+)", r.text or "")
    return m.group(1) if m else None


def oauth_token(session, code, code_verifier):
    url = (
        f"{BASE}/access/oauth/token"
        f"?client_id=myAadhaar&grant_type=authorization_code"
        f"&code_verifier={code_verifier}&code={code}"
    )
    r = session.post(
        url,
        headers={
            "Origin": ORIGIN,
            "Referer": ORIGIN + "/",
            "Content-Type": "application/json",
        },
        data="",
        timeout=40,
    )
    return r.status_code, r.json() if r.content else {}


def auth_headers(token, app_id="MYAADHAAR"):
    return {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "appID": app_id,
        "Accept-Language": "en_IN",
        "Origin": ORIGIN,
        "Referer": ORIGIN + "/",
        "X-Request-ID": str(uuid.uuid4()),
    }


def fetch_profile(token, uid):
    r = requests.post(
        PROFILE_URL, headers=auth_headers(token), json={"uidNumber": uid},
        proxies=PROXIES, timeout=40,
    )
    return r.status_code, r.json() if r.content else {}


def fetch_pdf(token, uid, mask=False):
    r = requests.post(
        DOWNLOAD_URL,
        headers=auth_headers(token, "MYAADHAARREVAMP"),
        json={"uid": int(uid) if str(uid).isdigit() else uid, "mask": mask},
        proxies=PROXIES, timeout=90,
    )
    return r.status_code, r.json() if r.content else {}


def fetch_bank(token, uid):
    r = requests.post(
        BANK_URL, headers=auth_headers(token), json={"uid": str(uid)},
        proxies=PROXIES, timeout=40,
    )
    return r.status_code, r.json() if r.content else {}


def fetch_bio(token, uid):
    r = requests.post(
        BIO_URL, headers=auth_headers(token), json={"uid": str(uid)},
        proxies=PROXIES, timeout=40,
    )
    return r.status_code, r.json() if r.content else {}


def fetch_hof(token, uid):
    r = requests.post(
        HOF_URL, headers=auth_headers(token), json={"uidNumber": str(uid)},
        proxies=PROXIES, timeout=40,
    )
    return r.status_code, r.json() if r.content else {}


def captcha_v3():
    """X-Request-ID → verify.transactionId ; response.transactionId → captchaTxnId"""
    req_id = str(uuid.uuid4())
    r = requests.post(
        CAPTCHA_V3_URL,
        headers={
            "User-Agent": UA,
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": ORIGIN,
            "Referer": ORIGIN + "/",
            "appid": "MYAADHAAR",
            "X-Request-ID": req_id,
            "sec-ch-ua": '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
        },
        json={"captchaLength": "6", "captchaType": "2", "audioCaptchaRequired": True},
        proxies=PROXIES,
        timeout=40,
    )
    data = r.json() if r.content else {}
    log.info("captcha_v3 status=%s keys=%s req_id=%s", r.status_code, list(data.keys()), req_id)
    img = data.get("imageBase64") or ""
    captcha_txn = data.get("transactionId") or ""
    if img.startswith("data:"):
        img = img.split(",", 1)[-1]
    return base64.b64decode(img) if img else b"", captcha_txn, req_id, data


def verify_uid(uid, captcha, captcha_txn, transaction_id):
    payload = {
        "uid": str(uid),
        "captchaTxnId": str(captcha_txn),
        "captcha": str(captcha).strip(),
        "transactionId": str(transaction_id),
        "captchaLogic": "V3",
    }
    log.info("verify payload=%s", payload)
    r = requests.post(
        VERIFY_URL,
        headers={
            "User-Agent": UA,
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": ORIGIN,
            "Referer": ORIGIN + "/",
            "appid": "MYAADHAAR",
            "sec-ch-ua": '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
        },
        json=payload,
        proxies=PROXIES,
        timeout=40,
    )
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:300]}
    return r.status_code, body


def format_profile(data: dict) -> str:
    d = (data.get("responseData") or {}).get("demographicsInfo") or data
    return "\n".join([
        "✅ *Aadhaar Profile*",
        f"🆔 `{d.get('uid') or ''}`",
        f"👤 *Name:* {d.get('name') or ''}",
        f"📛 *Hindi:* {d.get('localResName') or ''}",
        f"⚧ *Gender:* {d.get('gender') or ''}",
        f"🎂 *DOB:* {d.get('dob') or ''}",
        f"📱 *Mobile:* {d.get('mobile') or ''}",
        f"📧 *Email:* {d.get('email') or '—'}",
        f"🏠 *Care of:* {d.get('careof') or ''}",
        f"📍 *Address:*",
        f"  {d.get('building') or ''} {d.get('street') or ''}",
        f"  {d.get('landmark') or ''}",
        f"  {d.get('vtcName') or ''}, {d.get('districtName') or ''}",
        f"  {d.get('stateName') or ''} - {d.get('pincode') or ''}",
        f"📌 *EID:* `{d.get('eid') or ''}`",
    ])


PDF_PASSWORD_HELP = (
    "🔐 *PDF Password (UIDAI official):*\n"
    "• First *4 letters* of name (CAPITAL)\n"
    "• + *Year of birth* (YYYY)\n\n"
    "Example: `ALOK1981`"
)


# ==================== HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "🪪 *UIDAI Aadhaar Bot By Abhigyan*\n\n"
        "12-digit Aadhaar bhejein:\n"
        "Example: `123456789123`\n\n"
        "Ya /verify se bina OTP basic check.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return AADHAAR


async def get_aadhaar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = (update.message.text or "").strip().replace(" ", "")
    if not (uid.isdigit() and len(uid) == 12):
        await update.message.reply_text("❌ Valid 12-digit Aadhaar bhejein.")
        return AADHAAR

    context.user_data["uid"] = uid
    msg = await update.message.reply_text("⏳ Fresh session + captcha...")

    try:
        verifier, challenge = make_pkce()
        telemetry_id = str(uuid.uuid4())
        session = new_session(challenge, telemetry_id)
        img_bytes, txn = generate_captcha(session)
        context.user_data.update({
            "session": session,
            "captcha_txn": txn,
            "code_verifier": verifier,
            "code_challenge": challenge,
            "telemetry_id": telemetry_id,
        })
        await msg.delete()
        await update.message.reply_photo(
            photo=BytesIO(img_bytes),
            caption="🔐 Captcha type karke bhejein:",
        )
        return CAPTCHA
    except Exception as e:
        log.exception("captcha")
        try:
            await msg.edit_text(f"❌ Captcha fail: {e}\n/start")
        except Exception:
            await update.message.reply_text(f"❌ Captcha fail: {e}\n/start")
        return ConversationHandler.END


async def get_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Captcha text bhejein.")
        return CAPTCHA

    uid = context.user_data["uid"]
    session = context.user_data["session"]
    txn = context.user_data["captcha_txn"]
    msg = await update.message.reply_text("⏳ OTP bheja ja raha hai...")

    try:
        status, data = send_otp(session, uid, text, txn)
        log.info("OTP %s %s", status, data)
        if status != 200 or not data.get("status"):
            err = data.get("message") or data.get("errorMessage") or str(data)
            await msg.edit_text(f"❌ OTP fail: {err}\n/start")
            return ConversationHandler.END
        # save otp txn for login bind
        context.user_data["otp_txn"] = data.get("txnId")
        await msg.edit_text(
            "✅ OTP registered mobile pe bhej diya.\n\n"
            "⚡ *Jaldi* 6-digit OTP bhejein (1–2 min ke andar):",
            parse_mode="Markdown",
        )
        return OTP
    except Exception as e:
        log.exception("otp")
        await msg.edit_text(f"❌ Error: {e}\n/start")
        return ConversationHandler.END


async def get_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    otp = (update.message.text or "").strip()
    if not (otp.isdigit() and len(otp) >= 6):
        await update.message.reply_text("Valid OTP bhejein.")
        return OTP

    uid = context.user_data["uid"]
    session = context.user_data["session"]
    otp_txn = context.user_data.get("otp_txn")
    msg = await update.message.reply_text("⏳ Login...")

    try:
        st, login_data = login_otp(session, uid, otp, otp_txn)
        log.info("login try1 %s %s", st, login_data)

        # Session already active → wait + retry SAME otp once (same cookies)
        if (
            login_data.get("errorCode") == "UOS-AUT-001"
            or "already active" in (login_data.get("errorMessage") or "").lower()
        ):
            await msg.edit_text("⚠️ Session busy… 5 sec baad retry...")
            time.sleep(5)
            st, login_data = login_otp(session, uid, otp, otp_txn)
            log.info("login try2 %s %s", st, login_data)

        if (
            login_data.get("errorCode") == "UOS-AUT-001"
            or "already active" in (login_data.get("errorMessage") or "").lower()
        ):
            # Clear local session; user must wait for UIDAI server lock
            context.user_data.pop("session", None)
            await msg.edit_text(
                "❌ UIDAI pe is Aadhaar ki session abhi *locked* hai "
                "(pehle incomplete login / browser open).\n\n"
                "👉 *2–3 minute wait* karke /start se *naya* captcha+OTP lo.\n"
                "Browser pe myAadhaar khula ho to band kar do.",
                parse_mode="Markdown",
            )
            return ConversationHandler.END

        if st != 200 or login_data.get("status") != "Y":
            err = login_data.get("errorMessage") or str(login_data)
            await msg.edit_text(f"❌ Login fail: {err}\n/start")
            return ConversationHandler.END

        challenge = context.user_data["code_challenge"]
        telemetry = context.user_data.get("telemetry_id") or ""
        redirect = login_data.get("redirectURL") or ""
        m = re.search(r"telemetry_session_id=([^&\s]+)", redirect)
        if m:
            telemetry = m.group(1)

        code = fetch_code(session, challenge, telemetry)
        if not code:
            await msg.edit_text("❌ OAuth code nahi mila.\n/start")
            return ConversationHandler.END

        st2, tok = oauth_token(session, code, context.user_data["code_verifier"])
        access = tok.get("access_token")
        if not access:
            await msg.edit_text(f"❌ Token fail: {tok}\n/start")
            return ConversationHandler.END

        context.user_data["access_token"] = access
        try:
            await msg.delete()
        except Exception:
            pass
        await update.message.reply_text(
            f"✅ Login success!\n🆔 `{uid}`\n\nNeeche se option choose karo:",
            parse_mode="Markdown",
            reply_markup=MENU_KEYBOARD,
        )
        return MENU
    except Exception as e:
        log.exception("otp verify")
        try:
            await msg.edit_text(f"❌ Error: {e}\n/start")
        except Exception:
            await update.message.reply_text(f"❌ Error: {e}\n/start")
        return ConversationHandler.END


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    uid = context.user_data.get("uid")
    token = context.user_data.get("access_token")

    if text in ("🔄 New Aadhaar", "/start"):
        context.user_data.clear()
        await update.message.reply_text(
            "12-digit Aadhaar bhejein:", reply_markup=ReplyKeyboardRemove()
        )
        return AADHAAR

    if text in ("❌ Close", "/cancel"):
        context.user_data.clear()
        await update.message.reply_text(
            "Closed. /start", reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    if not token or not uid:
        await update.message.reply_text(
            "Session expire. /start", reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    if "Profile" in text or text.startswith("1️⃣"):
        msg = await update.message.reply_text("⏳ Profile...")
        try:
            st, profile = fetch_profile(token, uid)
            if st != 200 or profile.get("status") != "Success":
                await msg.edit_text(f"❌ {profile}")
                return MENU
            await msg.delete()
            await update.message.reply_text(format_profile(profile), parse_mode="Markdown")
            photo_b64 = (
                (profile.get("responseData") or {})
                .get("demographicsInfo", {})
                .get("residentPhoto")
            )
            if photo_b64:
                try:
                    await update.message.reply_photo(
                        photo=BytesIO(base64.b64decode(photo_b64)),
                        caption="📷 Resident photo",
                    )
                except Exception:
                    pass
        except Exception as e:
            await msg.edit_text(f"❌ {e}")
        return MENU

    if "PDF" in text or text.startswith("2️⃣"):
        msg = await update.message.reply_text("⏳ PDF...")
        try:
            st, data = fetch_pdf(token, uid, mask=False)
            if st != 200 or data.get("status") != "Success":
                await msg.edit_text(f"❌ PDF fail: {data}")
                return MENU
            pdf_b64 = (data.get("data") or {}).get("aadhaarPdf") or ""
            eid = (data.get("data") or {}).get("eid") or ""
            vid = (data.get("data") or {}).get("vid") or ""
            if not pdf_b64:
                await msg.edit_text("❌ PDF empty")
                return MENU
            await msg.delete()
            await update.message.reply_document(
                document=BytesIO(base64.b64decode(pdf_b64)),
                filename=f"aadhaar_{uid}.pdf",
                caption=(
                    f"✅ Aadhaar PDF\n🆔 `{uid}`\n"
                    f"📌 EID: `{eid}`\n🔢 VID: `{vid}`\n\n{PDF_PASSWORD_HELP}"
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            log.exception("pdf")
            await msg.edit_text(f"❌ {e}")
        return MENU

    if "Bank" in text or text.startswith("3️⃣"):
        msg = await update.message.reply_text("⏳ Bank...")
        try:
            st, data = fetch_bank(token, uid)
            rd = (data.get("responseData") or {}).get("bankSeedingDetails") or {}
            if st != 200 or data.get("status") != "Success":
                await msg.edit_text(f"❌ {data}")
                return MENU
            sm = {"A": "Active / Seeded ✅", "N": "Not seeded ❌", "I": "In progress"}
            sc = rd.get("status") or ""
            await msg.edit_text(
                f"🏦 *Bank Seeding*\n🆔 `{uid}`\n"
                f"🏛 Bank: {rd.get('bankName') or '—'}\n"
                f"📊 Status: {sm.get(sc, sc)}\n"
                f"📅 Updated: {rd.get('lastUpdatedDate') or '—'}",
                parse_mode="Markdown",
            )
        except Exception as e:
            await msg.edit_text(f"❌ {e}")
        return MENU

    if "Bio" in text or text.startswith("4️⃣"):
        msg = await update.message.reply_text("⏳ Bio-lock...")
        try:
            st, data = fetch_bio(token, uid)
            rd = data.get("responseData") or {}
            if st != 200 or data.get("status") != "Success":
                await msg.edit_text(f"❌ {data}")
                return MENU

            def yn(v):
                return "ON ✅" if v else "OFF ❌"

            await msg.edit_text(
                f"🔏 *Bio-Lock*\n🆔 `{uid}`\n"
                f"• Bio lock: {yn(rd.get('enableBiometricsLock'))}\n"
                f"• Auth channel lock: {yn(rd.get('lockAuthChannel'))}\n"
                f"• Notify bio: {yn(rd.get('notifyOnBioAuth'))}\n"
                f"• Notify OTP: {yn(rd.get('notifyOnOTPAuth'))}\n"
                f"• Notify demo: {yn(rd.get('notifyOnDemoAuth'))}\n"
                f"• Notify PIN: {yn(rd.get('notifyOnPinAuth'))}\n"
                f"• Notify update: {yn(rd.get('notifyOnUpdate'))}",
                parse_mode="Markdown",
            )
        except Exception as e:
            await msg.edit_text(f"❌ {e}")
        return MENU

    if "Family" in text or text.startswith("5️⃣"):
        msg = await update.message.reply_text("⏳ HOF...")
        try:
            st, data = fetch_hof(token, uid)
            rd = data.get("responseData")
            if st != 200 or data.get("status") != "Success":
                await msg.edit_text(f"❌ {data}")
                return MENU
            if not rd:
                await msg.edit_text(
                    f"👨‍👩‍👧 *HOF*\n🆔 `{uid}`\n\n— No HOF linked —",
                    parse_mode="Markdown",
                )
            else:
                await msg.edit_text(
                    f"👨‍👩‍👧 *HOF*\n🆔 `{uid}`\n\n`{json.dumps(rd, ensure_ascii=False)[:2500]}`",
                    parse_mode="Markdown",
                )
        except Exception as e:
            await msg.edit_text(f"❌ {e}")
        return MENU

    if "Verify" in text or text.startswith("6️⃣"):
        msg = await update.message.reply_text("⏳ Captcha...")
        try:
            img, captcha_txn, req_id, raw = captcha_v3()
            if not img or not captcha_txn:
                await msg.edit_text(f"❌ Captcha fail keys={list(raw.keys())}")
                return MENU
            context.user_data["verify_txn"] = captcha_txn
            context.user_data["verify_tid"] = req_id
            await msg.delete()
            await update.message.reply_photo(
                photo=BytesIO(img),
                caption="🔐 Basic verify captcha:",
                reply_markup=ReplyKeyboardRemove(),
            )
            return VERIFY_CAPTCHA
        except Exception as e:
            await msg.edit_text(f"❌ {e}")
            return MENU

    await update.message.reply_text("Menu se choose karo.", reply_markup=MENU_KEYBOARD)
    return MENU


async def verify_captcha_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    captcha = (update.message.text or "").strip()
    uid = context.user_data.get("uid")
    captcha_txn = context.user_data.get("verify_txn")
    tid = context.user_data.get("verify_tid")
    msg = await update.message.reply_text("⏳ Verifying...")
    try:
        st, data = verify_uid(uid, captcha, captcha_txn, tid)
        log.info("verify %s %s", st, data)
        try:
            await msg.delete()
        except Exception:
            pass
        if data.get("status") == "Success" or data.get("aadhaarStatusCode"):
            await update.message.reply_text(
                f"✅ *Aadhaar Exists*\n"
                f"🆔 `{uid}`\n"
                f"📊 {data.get('statusMessage') or 'Exists'}\n"
                f"⚧ Gender: {data.get('gender') or '—'}\n"
                f"📅 Age band: {data.get('ageBand') or '—'}\n"
                f"📍 State: {data.get('address') or '—'}\n"
                f"📱 Mobile: {data.get('maskedMobileNumber') or '—'}",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove(),
            )
            return ConversationHandler.END
        err = (
            (data.get("errorDetails") or {}).get("messageEnglish")
            or data.get("errorCode")
            or str(data)
        )
        await update.message.reply_text(f"❌ {err}\n/verify")
        return ConversationHandler.END
    except Exception as e:
        log.exception("verify")
        try:
            await msg.delete()
        except Exception:
            pass
        await update.message.reply_text(f"❌ {e}")
        return ConversationHandler.END


async def cmd_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["mode"] = "basic_verify"
    await update.message.reply_text(
        "🔍 *Basic Verify* (no OTP)\n\n12-digit Aadhaar bhejein:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return AADHAAR


async def get_aadhaar_or_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("mode") == "basic_verify":
        uid = (update.message.text or "").strip().replace(" ", "")
        if not (uid.isdigit() and len(uid) == 12):
            await update.message.reply_text("❌ Valid 12-digit Aadhaar.")
            return AADHAAR
        context.user_data["uid"] = uid
        msg = await update.message.reply_text("⏳ Captcha...")
        try:
            img, captcha_txn, req_id, raw = captcha_v3()
            if not img or not captcha_txn:
                await msg.edit_text(f"❌ {str(raw)[:200]}")
                return ConversationHandler.END
            context.user_data["verify_txn"] = captcha_txn
            context.user_data["verify_tid"] = req_id
            await msg.delete()
            await update.message.reply_photo(
                photo=BytesIO(img), caption="🔐 Captcha type karke bhejein:"
            )
            return VERIFY_CAPTCHA
        except Exception as e:
            log.exception("v3")
            try:
                await msg.edit_text(f"❌ {e}")
            except Exception:
                await update.message.reply_text(f"❌ {e}")
            return ConversationHandler.END
    return await get_aadhaar(update, context)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Cancelled. /start", reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


def main():
    app = (
        Application.builder()
        .token(9115703725:AAG_hwSNAlKMBcT1YmfibdSzv7JNgeknflk)
        .connect_timeout(30)
        .read_timeout(90)
        .write_timeout(90)
        .build()
    )
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("verify", cmd_verify),
        ],
        states={
            AADHAAR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_aadhaar_or_verify)
            ],
            CAPTCHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_captcha)],
            OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_otp)],
            MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler)],
            VERIFY_CAPTCHA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, verify_captcha_handler)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        allow_reentry=True,
    )
    app.add_handler(conv)
    print("🚀 UIDAI Bot Full Features Started!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
