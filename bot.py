#!/usr/bin/env python3
"""
Promo Bot — Multi-usuario con licencias por key.
Env vars: BOT_TOKEN, API_ID, API_HASH, ADMIN_ID
"""
import os, json, time, asyncio, logging, secrets, string
from pathlib import Path
from datetime import datetime

from telegram import Update, InlineKeyboardButton as B, InlineKeyboardMarkup as KB
from telegram.ext import (Application, CommandHandler, MessageHandler,
                           CallbackQueryHandler, ConversationHandler,
                           filters, ContextTypes)
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.tl.types import Channel, Chat

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────────
DIR      = Path(__file__).parent
SESS_DIR = DIR / "sessions"
SESS_DIR.mkdir(exist_ok=True)
STATE    = DIR / "state.json"

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID    = int(os.environ["API_ID"])
API_HASH  = os.environ["API_HASH"]
ADMIN_ID  = int(os.environ["ADMIN_ID"])

DURATIONS = {
    "1d":  (86_400,      "1 día"),
    "7d":  (604_800,     "1 semana"),
    "30d": (2_592_000,   "1 mes"),
}
IVALS = [(1,"1 min"),(5,"5 min"),(10,"10 min"),(15,"15 min"),
         (30,"30 min"),(60,"1 h"),(120,"2 h"),(360,"6 h"),(720,"12 h"),(1440,"24 h")]

# ── State helpers ────────────────────────────────────────────────────────────
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
        s["users"][k] = {"key": None, "expires": 0, "auth_name": None,
                         "interval": 60, "delay": 3, "enabled": False, "last_sent": 0}
    return s["users"][k]

def key_ok(u: dict) -> bool:
    return bool(u.get("key")) and time.time() < u.get("expires", 0)

def fmt_date(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m/%Y")

def session_path(uid: int) -> str:
    return str(SESS_DIR / f"u{uid}")

# ── Telethon: pool de clientes persistentes ──────────────────────────────────
_clients: dict[int, TelegramClient] = {}

async def get_client(uid: int) -> TelegramClient:
    """Devuelve el cliente del usuario, creándolo o reconectándolo si hace falta."""
    cl = _clients.get(uid)
    if cl is None:
        cl = TelegramClient(session_path(uid), API_ID, API_HASH)
        _clients[uid] = cl
    if not cl.is_connected():
        await cl.connect()
    return cl

# ── Key generator ─────────────────────────────────────────────────────────────
def gen_key() -> str:
    abc = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(abc) for _ in range(10))

# ── Conversation states ───────────────────────────────────────────────────────
WAIT_KEY, PHONE, CODE, PASS2FA = range(4)

# ── /start ────────────────────────────────────────────────────────────────────
async def send_menu(msg, uid: int):
    """Envía el menú principal directamente a un mensaje. Nunca falla silenciosamente."""
    s = load()
    u = get_user(s, uid)
    if not key_ok(u):
        await msg.reply_text("⏰ Tu clave ha expirado. Contacta al administrador.")
        return
    text = f"🤖 *Promo Bot*\n👤 {u.get('auth_name', '?')}\n\nElige una opción:"
    await msg.reply_text(text, reply_markup=user_kb(u), parse_mode="Markdown")

async def cmd_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = upd.effective_user.id
    if uid == ADMIN_ID:
        await admin_menu(upd, ctx)
        return ConversationHandler.END
    s = load()
    u = get_user(s, uid)
    if key_ok(u) and u.get("auth_name"):
        await send_menu(upd.message, uid)
        return ConversationHandler.END
    if key_ok(u):
        await upd.message.reply_text(
            "📱 Ingresa tu número de teléfono (ej: `+52155...`):", parse_mode="Markdown")
        return PHONE
    await upd.message.reply_text("🔑 Ingresa tu *clave de acceso*:", parse_mode="Markdown")
    return WAIT_KEY

async def cmd_menu(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Comando /menu — siempre funciona independiente del ConversationHandler."""
    uid = upd.effective_user.id
    if uid == ADMIN_ID:
        await admin_menu(upd, ctx)
        return
    s = load()
    u = get_user(s, uid)
    if not u.get("auth_name"):
        await upd.message.reply_text("⚠️ Primero autentícate con /start")
        return
    await send_menu(upd.message, uid)

# ── Key validation ────────────────────────────────────────────────────────────
async def recv_key(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = upd.effective_user.id
    key = upd.message.text.strip().upper()
    s   = load()
    if key not in s["keys"] or time.time() > s["keys"][key]:
        await upd.message.reply_text("❌ Clave inválida o expirada. Inténtalo de nuevo:")
        return WAIT_KEY
    u = get_user(s, uid)
    u["key"]     = key
    u["expires"] = s["keys"][key]
    save(s)
    await upd.message.reply_text(
        f"✅ Clave válida hasta *{fmt_date(u['expires'])}*\n\n"
        "📱 Ingresa tu número de teléfono (ej: `+52155...`):", parse_mode="Markdown")
    return PHONE

# ── Auth flow ─────────────────────────────────────────────────────────────────
async def recv_phone(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid   = upd.effective_user.id
    phone = upd.message.text.strip()
    ctx.user_data["phone"] = phone
    try:
        cl = await get_client(uid)
        await cl.send_code_request(phone)
        await upd.message.reply_text("📱 Código enviado. Ingrésalo:")
    except Exception as e:
        await upd.message.reply_text(f"❌ Error: {e}\nEscribe /start para reintentar.")
        return ConversationHandler.END
    return CODE

async def recv_code(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid   = upd.effective_user.id
    code  = upd.message.text.strip().replace(" ", "")
    phone = ctx.user_data.get("phone", "")
    cl    = await get_client(uid)
    try:
        await cl.sign_in(phone, code)
    except SessionPasswordNeededError:
        await upd.message.reply_text("🔐 Ingresa tu contraseña 2FA:")
        return PASS2FA
    except Exception as e:
        await upd.message.reply_text(f"❌ {e}\nEscribe /start para reintentar.")
        return ConversationHandler.END
    return await finish_auth(upd, uid, cl)

async def recv_pass(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = upd.effective_user.id
    cl  = await get_client(uid)
    try:
        await cl.sign_in(password=upd.message.text)
    except Exception as e:
        await upd.message.reply_text(f"❌ Contraseña incorrecta: {e}")
        return PASS2FA
    return await finish_auth(upd, uid, cl)

async def finish_auth(upd: Update, uid: int, cl: TelegramClient):
    me = await cl.get_me()
    s  = load()
    u  = get_user(s, uid)
    u["auth_name"] = f"{me.first_name} (@{me.username})"
    save(s)
    log.info(f"Auth OK uid={uid} name={u['auth_name']}")
    await upd.message.reply_text(f"✅ Autenticado como *{u['auth_name']}*!", parse_mode="Markdown")
    await send_menu(upd.message, uid)   # directo, sin pasar por user_menu
    return ConversationHandler.END

# ── User menu ─────────────────────────────────────────────────────────────────
def user_kb(u: dict) -> KB:
    icon = "✅" if u["enabled"] else "⬜"
    return KB([
        [B("📋 Ver grupos",       callback_data="u_groups"),
         B("📤 Enviar ahora",     callback_data="u_send")],
        [B(f"{icon} Auto-envío",  callback_data="u_toggle"),
         B("⏰ Intervalo",        callback_data="u_interval")],
        [B(f"📅 Vence: {fmt_date(u['expires'])}", callback_data="noop")],
    ])

async def user_menu(upd: Update, _ctx, txt: str = ""):
    uid = upd.effective_user.id
    s   = load()
    u   = get_user(s, uid)
    if not key_ok(u):
        msg = "⏰ Tu clave ha expirado. Contacta al administrador."
        if upd.callback_query:
            await upd.callback_query.edit_message_text(msg)
        elif upd.effective_message:
            await upd.effective_message.reply_text(msg)
        return
    text = txt or f"🤖 *Promo Bot*\n👤 {u.get('auth_name','?')}\n\nElige una opción:"
    kb   = user_kb(u)
    if upd.callback_query:
        await upd.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await upd.effective_message.reply_text(text, reply_markup=kb, parse_mode="Markdown")

# ── Admin menu ────────────────────────────────────────────────────────────────
async def admin_menu(upd: Update, _ctx, txt: str = ""):
    text = txt or "👑 *Admin Panel*\n\nGenera una key o revisa usuarios:"
    kb   = KB([
        [B("🔑 Key 1 día",    callback_data="ak_1d"),
         B("🔑 Key 1 semana", callback_data="ak_7d"),
         B("🔑 Key 1 mes",    callback_data="ak_30d")],
        [B("👥 Ver usuarios", callback_data="a_users")],
    ])
    if upd.callback_query:
        await upd.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await upd.effective_message.reply_text(text, reply_markup=kb, parse_mode="Markdown")

# ── Callbacks ─────────────────────────────────────────────────────────────────
async def on_cb(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = upd.callback_query
    await q.answer()
    uid  = upd.effective_user.id
    data = q.data

    if data == "noop":
        return

    # ── Admin ──────────────────────────────────────────────────────────────
    if uid == ADMIN_ID:
        if data.startswith("ak_"):
            dur  = data[3:]
            secs, lbl = DURATIONS[dur]
            key  = gen_key()
            s    = load()
            exp  = int(time.time()) + secs
            s["keys"][key] = exp
            save(s)
            await q.edit_message_text(
                f"🔑 *Key nueva — {lbl}*\n\n`{key}`\n\nVence: {fmt_date(exp)}",
                parse_mode="Markdown",
                reply_markup=KB([[B("⬅️ Admin", callback_data="a_menu")]]))

        elif data == "a_users":
            s = load()
            rows = []
            for uid2, u in s.get("users", {}).items():
                ok  = "✅" if key_ok(u) else "❌"
                exp = fmt_date(u.get("expires", 0))
                rows.append(f"{ok} {u.get('auth_name') or uid2} · vence {exp}")
            text = ("👥 *Usuarios*\n\n" + "\n".join(rows)) if rows else "👥 Sin usuarios aún."
            await q.edit_message_text(text, parse_mode="Markdown",
                                      reply_markup=KB([[B("⬅️ Admin", callback_data="a_menu")]]))

        elif data == "a_menu":
            await admin_menu(upd, ctx)
        return

    # ── User ───────────────────────────────────────────────────────────────
    s = load()
    u = get_user(s, uid)
    if not key_ok(u):
        await q.edit_message_text("⏰ Tu clave ha expirado. Contacta al administrador.")
        return

    if data == "u_groups":
        await q.edit_message_text("🔄 Cargando grupos…")
        try:
            cl = await get_client(uid)
            gs = [d.name async for d in cl.iter_dialogs()
                  if isinstance(d.entity, (Channel, Chat))]
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
        await user_menu(upd, ctx, f"Auto-envío {st}")

    elif data == "u_interval":
        rows = [IVALS[i:i+3] for i in range(0, len(IVALS), 3)]
        kb   = KB([[B(l, callback_data=f"ui_{v}") for v, l in row] for row in rows]
                  + [[B("⬅️ Menú", callback_data="u_menu")]])
        await q.edit_message_text("⏰ *Selecciona el intervalo:*",
                                  reply_markup=kb, parse_mode="Markdown")

    elif data.startswith("ui_"):
        u["interval"] = int(data[3:])
        save(s)
        await user_menu(upd, ctx)

    elif data == "u_menu":
        await user_menu(upd, ctx)

# ── Send helper ───────────────────────────────────────────────────────────────
async def do_send(uid: int, delay: int) -> tuple[int, int]:
    cl    = await get_client(uid)
    sent  = errs = 0
    msgs  = [m async for m in cl.iter_messages("me", limit=30) if m.text or m.media]
    groups = [d.entity async for d in cl.iter_dialogs()
              if isinstance(d.entity, (Channel, Chat))]
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
                if not u.get("enabled"):
                    continue
                if now > u.get("expires", 0):    # key expirada, no enviar
                    continue
                if (now - u.get("last_sent", 0)) / 60 < u.get("interval", 60):
                    continue
                uid = int(uid_s)
                sent, errs = await do_send(uid, u.get("delay", 3))
                s2 = load()
                s2["users"][uid_s]["last_sent"] = now
                save(s2)
                log.info(f"uid={uid}: {sent} enviados, {errs} errores")
                await app.bot.send_message(uid, f"⏰ Envío automático: ✅ {sent}  ❌ {errs}")
            except Exception as e:
                log.error(f"Scheduler uid={uid_s}: {e}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    async def on_init(app: Application):
        asyncio.create_task(scheduler(app))

    app = Application.builder().token(BOT_TOKEN).post_init(on_init).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            WAIT_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_key)],
            PHONE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_phone)],
            CODE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_code)],
            PASS2FA:  [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_pass)],
        },
        fallbacks=[CommandHandler("start", cmd_start),
                   CommandHandler("menu",  cmd_menu)],
        allow_reentry=True,
    )
    app.add_handler(conv)
    # /menu funciona siempre, incluso fuera del ConversationHandler
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CallbackQueryHandler(on_cb))

    log.info("Bot iniciado")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
