#!/usr/bin/env python3
"""
Promo Bot — Multi-usuario con licencias por key.
Env vars: BOT_TOKEN, API_ID, API_HASH, ADMIN_ID
"""
import os, json, time, asyncio, logging, secrets, string
from pathlib import Path
from datetime import datetime

from telegram import Update, InlineKeyboardButton as B, InlineKeyboardMarkup as KB
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.tl.types import Channel, Chat

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
DIR      = Path(__file__).parent
SESS_DIR = DIR / "sessions"
SESS_DIR.mkdir(exist_ok=True)
STATE    = DIR / "state.json"

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID    = int(os.environ["API_ID"])
API_HASH  = os.environ["API_HASH"]
ADMIN_ID  = int(os.environ["ADMIN_ID"])

DURATIONS = {"1d": (86_400, "1 día"), "7d": (604_800, "1 semana"), "30d": (2_592_000, "1 mes")}
IVALS = [(1,"1 min"),(5,"5 min"),(10,"10 min"),(15,"15 min"),
         (30,"30 min"),(60,"1 h"),(120,"2 h"),(360,"6 h"),(720,"12 h"),(1440,"24 h")]

# ── State ────────────────────────────────────────────────────────────────────
def load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"keys": {}, "users": {}}

def save(s: dict):
    STATE.write_text(json.dumps(s, ensure_ascii=False))

def get_user(s: dict, uid: int) -> dict:
    k = str(uid)
    if k not in s["users"]:
        s["users"][k] = {
            "step": "need_key",   # need_key | need_phone | need_code | need_pass | ready
            "key": None, "expires": 0, "phone": None, "auth_name": None,
            "interval": 60, "delay": 3, "enabled": False, "last_sent": 0,
        }
    return s["users"][k]

def key_ok(u: dict) -> bool:
    return bool(u.get("key")) and time.time() < u.get("expires", 0)

def fmt_date(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m/%Y")

def session_path(uid: int) -> str:
    return str(SESS_DIR / f"u{uid}")

# ── Telethon pool persistente ─────────────────────────────────────────────────
_clients: dict[int, TelegramClient] = {}

async def get_client(uid: int) -> TelegramClient:
    cl = _clients.get(uid)
    if cl is None:
        cl = TelegramClient(session_path(uid), API_ID, API_HASH)
        _clients[uid] = cl
    if not cl.is_connected():
        await cl.connect()
    return cl

# ── Key generator ─────────────────────────────────────────────────────────────
def gen_key() -> str:
    return "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))

# ── Keyboards ─────────────────────────────────────────────────────────────────
def user_kb(u: dict) -> KB:
    icon = "✅" if u["enabled"] else "⬜"
    return KB([
        [B("📋 Ver grupos", callback_data="u_groups"), B("📤 Enviar ahora", callback_data="u_send")],
        [B(f"{icon} Auto-envío", callback_data="u_toggle"), B("⏰ Intervalo", callback_data="u_interval")],
        [B(f"📅 Vence: {fmt_date(u['expires'])}", callback_data="noop")],
    ])

def admin_kb() -> KB:
    return KB([
        [B("🥀 KEY 1 DIA", callback_data="ak_1d"), B("🫪 KEY 1 SEMANA", callback_data="ak_7d"),
         B("💎 KEY 1 MES", callback_data="ak_30d")],
        [B("👥 Ver usuarios", callback_data="a_users")],
    ])

# ── Enviar menú ───────────────────────────────────────────────────────────────
async def show_user_menu(msg, uid: int, txt: str = ""):
    s = load()
    u = get_user(s, uid)
    if not key_ok(u):
        await msg.reply_text("⏰ Tu clave ha expirado. Contacta al administrador.")
        return
    text = txt or f"🤖 DARK BOT*\n👤 {u.get('auth_name','?')}\n\nElige una opción:"
    await msg.reply_text(text, reply_markup=user_kb(u), parse_mode="Markdown")

async def show_admin_menu(msg, txt: str = ""):
    text = txt or "👑 *Admin Panel*\n\nGenera una key o revisa usuarios:"
    await msg.reply_text(text, reply_markup=admin_kb(), parse_mode="Markdown")

# ── /start y /menu ────────────────────────────────────────────────────────────
async def cmd_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = upd.effective_user.id
    msg = upd.message
    log.info(f"/start uid={uid}")

    if uid == ADMIN_ID:
        await show_admin_menu(msg)
        return

    s = load()
    u = get_user(s, uid)
    save(s)  # persiste el usuario nuevo si recién se creó

    step = u.get("step", "need_key")

    if step == "ready" and key_ok(u):
        await show_user_menu(msg, uid)
    elif step == "ready" and not key_ok(u):
        u["step"] = "need_key"
        save(s)
        await msg.reply_text("⏰ Tu clave expiró.\n\n🔑 Ingresa tu nueva *clave de acceso*:", parse_mode="Markdown")
    elif step == "need_phone":
        await msg.reply_text("📱 Ingresa tu número de teléfono (ej: `+52155...`):", parse_mode="Markdown")
    elif step == "need_code":
        await msg.reply_text("📱 Ingresa el código que recibiste de Telegram:")
    elif step == "need_pass":
        await msg.reply_text("🔐 Ingresa tu contraseña 2FA:")
    else:  # need_key o cualquier otro
        await msg.reply_text("🔑 Ingresa tu *clave de acceso*:", parse_mode="Markdown")

async def cmd_menu(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = upd.effective_user.id
    msg = upd.message
    if uid == ADMIN_ID:
        await show_admin_menu(msg)
        return
    s = load()
    u = get_user(s, uid)
    if u.get("step") == "ready" and key_ok(u):
        await show_user_menu(msg, uid)
    else:
        await msg.reply_text("⚠️ Usa /start para autenticarte primero.")

# ── Manejador de texto (estado propio, sin ConversationHandler) ───────────────
async def handle_text(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = upd.effective_user.id
    msg  = upd.message
    text = msg.text.strip()

    if uid == ADMIN_ID:
        return  # el admin no manda texto en el flujo de auth

    s = load()
    u = get_user(s, uid)
    step = u.get("step", "need_key")
    log.info(f"text uid={uid} step={step}")

    # ── need_key ──────────────────────────────────────────────────────────────
    if step == "need_key":
        key = text.upper()
        if key not in s["keys"] or time.time() > s["keys"][key]:
            await msg.reply_text("❌ Clave inválida o expirada. Inténtalo de nuevo:")
            return
        u["key"]     = key
        u["expires"] = s["keys"][key]
        u["step"]    = "need_phone"
        save(s)
        await msg.reply_text(
            f"✅ Clave válida hasta *{fmt_date(u['expires'])}*\n\n"
            "📱 Ingresa tu número de teléfono (ej: `+52155...`):", parse_mode="Markdown")

    # ── need_phone ────────────────────────────────────────────────────────────
    elif step == "need_phone":
        phone = text
        try:
            cl = await get_client(uid)
            await cl.send_code_request(phone)
            u["phone"] = phone
            u["step"]  = "need_code"
            save(s)
            await msg.reply_text("📱 Código enviado. Ingrésalo:")
        except Exception as e:
            await msg.reply_text(f"❌ Error: {e}\nIntenta de nuevo con tu número:")

    # ── need_code ─────────────────────────────────────────────────────────────
    elif step == "need_code":
        code  = text.replace(" ", "")
        phone = u.get("phone", "")
        try:
            cl = await get_client(uid)
            await cl.sign_in(phone, code)
            await _complete_auth(msg, uid, u, s)
        except SessionPasswordNeededError:
            u["step"] = "need_pass"
            save(s)
            await msg.reply_text("🔐 Tienes 2FA activo. Ingresa tu contraseña:")
        except Exception as e:
            await msg.reply_text(f"❌ Código incorrecto: {e}\nInténtalo de nuevo:")

    # ── need_pass ─────────────────────────────────────────────────────────────
    elif step == "need_pass":
        try:
            cl = await get_client(uid)
            await cl.sign_in(password=text)
            await _complete_auth(msg, uid, u, s)
        except Exception as e:
            await msg.reply_text(f"❌ Contraseña incorrecta: {e}")

    # ── ready (no debería recibir texto libre) ────────────────────────────────
    else:
        await show_user_menu(msg, uid)

async def _complete_auth(msg, uid: int, u: dict, s: dict):
    cl = await get_client(uid)
    me = await cl.get_me()
    u["auth_name"] = f"{me.first_name} (@{me.username})"
    u["step"]      = "ready"
    save(s)
    log.info(f"Auth OK uid={uid} name={u['auth_name']}")
    await msg.reply_text(f"✅ Autenticado como *{u['auth_name']}*!", parse_mode="Markdown")
    await show_user_menu(msg, uid)

# ── Callbacks ─────────────────────────────────────────────────────────────────
async def on_cb(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = upd.callback_query
    await q.answer()
    uid  = upd.effective_user.id
    data = q.data

    if data == "noop":
        return

    # Admin
    if uid == ADMIN_ID:
        if data.startswith("ak_"):
            dur = data[3:]
            secs, lbl = DURATIONS[dur]
            key = gen_key()
            s   = load()
            exp = int(time.time()) + secs
            s["keys"][key] = exp
            save(s)
            await q.edit_message_text(
                f"🔑 *KEY GENERADA — {lbl}*\n\n`{key}`\nEXPIRA: {fmt_date(exp)}",
                parse_mode="Markdown",
                reply_markup=KB([[B("⬅️ REGRESAR", callback_data="a_menu")]]))

        elif data == "a_users":
            s    = load()
            rows = []
            for uid2, u in s.get("users", {}).items():
                ok  = "✅" if key_ok(u) else "❌"
                exp = fmt_date(u.get("expires", 0))
                rows.append(f"{ok} {u.get('auth_name') or uid2} · vence {exp}")
            text = ("👥 *Usuarios*\n\n" + "\n".join(rows)) if rows else "👥 Sin usuarios aún."
            await q.edit_message_text(text, parse_mode="Markdown",
                                      reply_markup=KB([[B("⬅️ Admin", callback_data="a_menu")]]))

        elif data == "a_menu":
            await q.edit_message_text("👑 *Admin Panel*\n\nGenera una key o revisa usuarios:",
                                      reply_markup=admin_kb(), parse_mode="Markdown")
        return

    # Usuario normal
    s = load()
    u = get_user(s, uid)
    if not key_ok(u):
        await q.edit_message_text("⏰ Tu clave ha expirado. Contacta al administrador.")
        return

    def refresh_menu(txt=""):
        text = txt or f"🤖 *Promo Bot*\n👤 {u.get('auth_name','?')}\n\nElige una opción:"
        return q.edit_message_text(text, reply_markup=user_kb(u), parse_mode="Markdown")

    if data == "u_groups":
        await q.edit_message_text("🔄 Cargando grupos…")
        try:
            cl = await get_client(uid)
            gs = [d.name async for d in cl.iter_dialogs() if isinstance(d.entity, (Channel, Chat))]
            text = (f"📋 *Grupos ({len(gs)})*\n\n" + "\n".join(f"• {g}" for g in gs)) if gs \
                   else "📋 No encontré grupos."
        except Exception as e:
            text = f"❌ {e}"
        await q.edit_message_text(text, parse_mode="Markdown",
                                  reply_markup=KB([[B("⬅️ Menú", callback_data="u_menu")]]))

    elif data == "u_send":
        await q.edit_message_text("📤 Enviando mensajes…")
        try:
            sent, errs = await do_send(uid, u["delay"])
            text = f"✅ *Envío completado*\n• Enviados: {sent}\n• Errores: {errs}"
        except Exception as e:
            text = f"❌ {e}"
        await q.edit_message_text(text, parse_mode="Markdown",
                                  reply_markup=KB([[B("⬅️ Menú", callback_data="u_menu")]]))

    elif data == "u_toggle":
        u["enabled"] = not u["enabled"]
        save(s)
        st = "activado ✅" if u["enabled"] else "desactivado ❌"
        await refresh_menu(f"Auto-envío {st}")

    elif data == "u_interval":
        rows = [IVALS[i:i+3] for i in range(0, len(IVALS), 3)]
        kb   = KB([[B(l, callback_data=f"ui_{v}") for v, l in row] for row in rows]
                  + [[B("⬅️ Menú", callback_data="u_menu")]])
        await q.edit_message_text("⏰ *Selecciona el intervalo:*", reply_markup=kb, parse_mode="Markdown")

    elif data.startswith("ui_"):
        u["interval"] = int(data[3:])
        save(s)
        await refresh_menu()

    elif data == "u_menu":
        await refresh_menu()

# ── Enviar mensajes (Telethon) ────────────────────────────────────────────────
async def do_send(uid: int, delay: int) -> tuple[int, int]:
    cl    = await get_client(uid)
    sent  = errs = 0
    msgs  = [m async for m in cl.iter_messages("me", limit=30) if m.text or m.media]
    groups = [d.entity async for d in cl.iter_dialogs() if isinstance(d.entity, (Channel, Chat))]
    for grp in groups:
        for msg in msgs:
            try:
                await cl.forward_messages(grp, msg)
                sent += 1
                await asyncio.sleep(delay)
            except Exception:
                errs += 1
    return sent, errs

# ── Scheduler ─────────────────────────────────────────────────────────────────
async def scheduler(app: Application):
    log.info("Scheduler iniciado")
    while True:
        await asyncio.sleep(30)
        now = time.time()
        s   = load()
        for uid_s, u in list(s.get("users", {}).items()):
            try:
                if u.get("step") != "ready" or not u.get("enabled"):
                    continue
                if now > u.get("expires", 0):
                    continue
                if (now - u.get("last_sent", 0)) / 60 < u.get("interval", 60):
                    continue
                uid = int(uid_s)
                sent, errs = await do_send(uid, u.get("delay", 3))
                s2 = load()
                s2["users"][uid_s]["last_sent"] = now
                save(s2)
                log.info(f"Scheduler uid={uid}: {sent} enviados, {errs} errores")
                await app.bot.send_message(uid, f"⏰ Envío automático: ✅ {sent}  ❌ {errs}")
            except Exception as e:
                log.error(f"Scheduler uid={uid_s}: {e}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    async def on_init(app: Application):
        asyncio.create_task(scheduler(app))

    app = Application.builder().token(BOT_TOKEN).post_init(on_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu",  cmd_menu))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    log.info("Bot iniciado")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
