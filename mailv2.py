import logging
import httpx
import random
import string
from faker import Faker
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from telegram.error import BadRequest

# Konfigurasi logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

fake = Faker('id_ID')

# Sesi pengguna
# structure:
#   {
#     chat_id: {
#       'provider': '1secmail' | 'mailtm',
#       'email': str,
#       'password': str,     # dummy utk 1secmail; real utk mail.tm
#       'base': str|None,    # mirror 1secmail yg berhasil
#       'messages': [...]
#     }
#   }
user_sessions = {}

# ============================================================
# BACKEND A: 1SECMail (mirror + UA + fallback create)
# ============================================================
MIRRORS_1SEC = [
    "https://www.1secmail.com/api/v1/",
    "https://www.1secmail.net/api/v1/",
    "https://www.1secmail.org/api/v1/",
]
UA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )
}
PUBLIC_1SEC_DOMAINS = [
    "1secmail.com", "1secmail.net", "1secmail.org",
    "esiix.com", "wwjmp.com", "oosln.com", "vddaz.com",
    "xojxe.com", "yoggm.com", "zsero.com", "txcct.com"
]

async def _1sec_get(params: dict, base_url_hint: str | None = None):
    mirrors = [base_url_hint] + MIRRORS_1SEC if base_url_hint else MIRRORS_1SEC
    last_err = "Tidak bisa menghubungi 1secmail (semua mirror)."
    for base in mirrors:
        try:
            async with httpx.AsyncClient(timeout=10, headers=UA_HEADERS, http2=True) as client:
                r = await client.get(base, params=params)
            if r.status_code == 200:
                return r.json(), base, None
            elif r.status_code in (401, 403):
                last_err = f"HTTP {r.status_code} dari {base}"
                continue
            else:
                last_err = f"HTTP {r.status_code} dari {base}"
        except Exception as e:
            last_err = f"Koneksi error ke {base}: {e}"
    return None, None, last_err

def _1sec_local_login():
    return (f"{fake.first_name().lower()}{fake.last_name().lower()}{random.randint(10,99)}").replace(" ", "")

async def create_email_1sec():
    # coba API genRandomMailbox
    data, used_base, err = await _1sec_get({"action": "genRandomMailbox", "count": 1})
    if not err and data:
        email = data[0]
        return {
            "provider": "1secmail",
            "email": email,
            "password": fake.password(length=12),  # dummy utk tampilan
            "base": used_base
        }, None
    # fallback: buat alamat lokal tanpa API create
    login = _1sec_local_login()
    domain = random.choice(PUBLIC_1SEC_DOMAINS)
    email = f"{login}@{domain}"
    return {
        "provider": "1secmail",
        "email": email,
        "password": fake.password(length=12),  # dummy
        "base": None
    }, None

async def auth_1sec(email: str):
    try:
        login, domain = email.split("@", 1)
        return {"provider": "1secmail", "login": login, "domain": domain}, None
    except ValueError:
        return None, "Format email tidak valid."

async def list_1sec(token_like: dict, base_hint: str | None = None):
    login, domain = token_like["login"], token_like["domain"]
    data, used_base, err = await _1sec_get(
        {"action": "getMessages", "login": login, "domain": domain},
        base_url_hint=base_hint
    )
    if err and data is None:
        return None, "Gagal mengambil daftar pesan."
    items = []
    for m in (data or []):
        items.append({
            "id": m.get("id"),
            "from": {"address": m.get("from", "")},
            "subject": m.get("subject", "(Tanpa subjek)")
        })
    return {"items": items, "base": used_base}, None

async def read_1sec(token_like: dict, message_id: str | int, base_hint: str | None = None):
    login, domain = token_like["login"], token_like["domain"]
    data, used_base, err = await _1sec_get(
        {"action": "readMessage", "login": login, "domain": domain, "id": message_id},
        base_url_hint=base_hint
    )
    if err or not data:
        return None, "Gagal mengambil isi pesan."
    text = (data.get("textBody") or data.get("body") or data.get("htmlBody") or "").strip()
    if not text and data.get("htmlBody"):
        text = data["htmlBody"]
    return {"item": {"subject": data.get("subject", "(Tanpa subjek)"), "text": text or "(Tidak ada isi pesan teks)"}, "base": used_base}, None


# ============================================================
# BACKEND B: mail.tm (fallback jika 1secmail diblok saat LIST)
# ============================================================

async def mtm_get_domains():
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://api.mail.tm/domains")
        if r.status_code == 200:
            arr = r.json().get('hydra:member', [])
            if arr:
                return random.choice(arr)['domain'], None
        return None, "Domain mail.tm kosong."
    except Exception as e:
        return None, f"Err domain mail.tm: {e}"

async def create_email_mailtm():
    domain, err = await mtm_get_domains()
    if err or not domain:
        return None, (err or "Gagal mengambil domain mail.tm")
    for _ in range(5):
        username = (f"{fake.first_name().lower()}{fake.last_name().lower()}").replace(" ", "")
        email = f"{username}@{domain}"
        password = fake.password(length=12)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post("https://api.mail.tm/accounts", json={"address": email, "password": password})
            if r.status_code == 201:
                return {"provider": "mailtm", "email": email, "password": password, "base": None}, None
            elif r.status_code == 422:
                continue
            else:
                return None, f"HTTP {r.status_code} saat create mail.tm"
        except Exception as e:
            return None, f"Err create mail.tm: {e}"
    return None, "Gagal menemukan nama unik mail.tm."

async def auth_mailtm(email: str, password: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post("https://api.mail.tm/token", json={"address": email, "password": password})
        if r.status_code == 200:
            return {"provider": "mailtm", "token": r.json().get('token')}, None
        return None, "Gagal login ke mail.tm"
    except Exception as e:
        return None, f"Err auth mail.tm: {e}"

async def list_mailtm(token_like: dict):
    headers = {'Authorization': f'Bearer {token_like["token"]}'}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://api.mail.tm/messages", headers=headers)
        if r.status_code != 200:
            return None, "Gagal mengambil daftar pesan."
        msgs = r.json().get('hydra:member', []) or []
        items = []
        for m in msgs:
            items.append({
                "id": m.get("id"),
                "from": {"address": m.get("from", {}).get("address", "")},
                "subject": m.get("subject", "(Tanpa subjek)")
            })
        return {"items": items}, None
    except Exception as e:
        return None, f"Err list mail.tm: {e}"

async def read_mailtm(token_like: dict, message_id: str):
    headers = {'Authorization': f'Bearer {token_like["token"]}'}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"https://api.mail.tm/messages/{message_id}", headers=headers)
        if r.status_code != 200:
            return None, "Gagal mengambil isi pesan."
        j = r.json()
        subject = j.get("subject", "(Tanpa subjek)")
        text = (j.get("text") or "").strip()
        if not text:
            text = j.get("intro") or "(Tidak ada isi pesan teks)"
        return {"item": {"subject": subject, "text": text}}, None
    except Exception as e:
        return None, f"Err read mail.tm: {e}"


# ============================================================
# PEMBUNGKUS PROVIDER (mempertahankan UI lama)
# ============================================================

async def create_temp_email():
    """
    Buat email dengan preferensi:
      1) 1secmail (mirror + UA)
         - setelah dibuat, uji 'getMessages' sekali.
         - jika blocked/403 => fallback ke mail.tm
      2) mail.tm
    """
    # 1) coba 1secmail
    res1, err1 = await create_email_1sec()
    if res1:
        # smoke test: bisa list messages?
        tk, e = await auth_1sec(res1["email"])
        if not e:
            test_list, e2 = await list_1sec(tk, base_hint=res1.get("base"))
            if not e2:  # sukses; pakai 1secmail
                return res1, None
            else:
                logger.warning(f"1secmail list blocked, fallback to mail.tm: {e2}")
        else:
            logger.warning(f"1secmail auth error, fallback to mail.tm: {e}")
    # 2) fallback mail.tm
    res2, err2 = await create_email_mailtm()
    if res2:
        return res2, None
    # keduanya gagal
    return None, (err2 or err1 or "Tidak bisa membuat email di provider manapun.")

async def get_auth_token(email, password, provider: str):
    if provider == "1secmail":
        return await auth_1sec(email)
    else:
        return await auth_mailtm(email, password)

async def fetch_messages(token_like, provider: str, base_url_hint: str | None = None):
    if provider == "1secmail":
        return await list_1sec(token_like, base_hint=base_url_hint)
    else:
        return await list_mailtm(token_like)

async def fetch_message_content(token_like, provider: str, message_id, base_url_hint: str | None = None):
    if provider == "1secmail":
        return await read_1sec(token_like, message_id, base_hint=base_url_hint)
    else:
        return await read_mailtm(token_like, message_id)


# ============================================================
# UI TELEGRAM (TIDAK DIUBAH TAMPILAN)
# ============================================================

def get_base_info_text(email, password, footer_text):
    return (
        f"┌─  *AKUN EMAIL ANDA* ─┐\n"
        f"│\n"
        f"│  📧  *Email*\n"
        f"│  `{email}`\n"
        f"│\n"
        f"│  🔑  *Password*\n"
        f"│  `{password}`\n"
        f"│\n"
        f"└─  _{footer_text}_ ─┘"
    )

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.message.from_user.first_name
    await update.message.reply_text(
        f"👋 Halo, *{user_name}*!\n\nKirim /buatemail untuk membuat email baru.", parse_mode='Markdown'
    )

async def buat_email_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    processing_message = await update.message.reply_text("⏳ Sedang membuat akun email Anda...")
    result, error = await create_temp_email()
    await context.bot.delete_message(chat_id=chat_id, message_id=processing_message.message_id)
    if result:
        user_sessions[chat_id] = {
            'provider': result['provider'],
            'email': result['email'],
            'password': result['password'],
            'base': result.get('base')
        }
        keyboard = [[InlineKeyboardButton("📬 Cek Inbox", callback_data="check_inbox_0")]]
        response_text = get_base_info_text(result['email'], result['password'], "Gunakan tombol di bawah untuk memeriksa inbox.")
        await update.message.reply_text(response_text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(f"❌ *Gagal Membuat Email*\n\n*Alasan:* {error}", parse_mode='Markdown')

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    session = user_sessions.get(chat_id)
    if not session:
        await query.edit_message_text("Sesi tidak ditemukan. Buat email baru dengan /buatemail.")
        return

    action_parts = query.data.split('_')
    action = action_parts[0]
    provider = session['provider']
    email, password = session['email'], session['password']
    response_text, reply_markup = None, None

    # === Cek Inbox ===
    if action == "check" and "inbox" in action_parts:
        token, error = await get_auth_token(email, password, provider)
        if error:
            await query.edit_message_text(f"Error: {error}")
            return

        messages_pack, error = await fetch_messages(token, provider, base_url_hint=session.get('base'))
        if error:
            # Jika provider 1secmail gagal list, otomatis pindah ke mail.tm
            if provider == "1secmail":
                fallback_result, fb_err = await create_email_mailtm()
                if fallback_result:
                    # update sesi => email baru (provider mail.tm)
                    user_sessions[chat_id] = {
                        'provider': fallback_result['provider'],
                        'email': fallback_result['email'],
                        'password': fallback_result['password'],
                        'base': None
                    }
                    # tampilkan info baru + tombol cek inbox
                    base_text = get_base_info_text(fallback_result['email'], fallback_result['password'],
                                                   "Provider utama sedang diblokir, akun baru dibuat otomatis.")
                    keyboard_list = [[InlineKeyboardButton("📬 Cek Inbox", callback_data="check_inbox_0")]]
                    await query.edit_message_text(text=base_text, parse_mode='Markdown',
                                                  reply_markup=InlineKeyboardMarkup(keyboard_list))
                    return
            await query.edit_message_text(f"Error: {error}")
            return

        messages = messages_pack["items"]
        if provider == "1secmail" and messages_pack.get("base"):
            user_sessions[chat_id]['base'] = messages_pack["base"]

        user_sessions[chat_id]['messages'] = messages

        base_text = get_base_info_text(email, password, "Inbox terakhir diperbarui...")
        inbox_text = "\n\n*Inbox Anda saat ini kosong.*"
        keyboard_list = [[InlineKeyboardButton(f"🔄 Refresh Inbox (0)", callback_data="check_inbox_0")]]

        if messages:
            inbox_text = "\n\n*Pesan yang diterima:*\n"
            keyboard_list = []
            for i, msg in enumerate(messages):
                sender = msg['from']['address']
                subject = msg.get('subject', '(Tanpa subjek)')
                inbox_text += f"*{i+1}.* Dari: `{sender}`\n    Subjek: _{subject}_\n"
                keyboard_list.append([InlineKeyboardButton(f"✉️ Buka Pesan #{i+1}", callback_data=f"open_message_{i}")])
            keyboard_list.append([InlineKeyboardButton(f"🔄 Refresh Inbox ({len(messages)})", callback_data="check_inbox_0")])

        response_text = base_text + inbox_text
        reply_markup = InlineKeyboardMarkup(keyboard_list)

    # === Buka Pesan ===
    elif action == "open" and "message" in action_parts:
        try:
            msg_index = int(action_parts[2])
            message_to_open = user_sessions[chat_id]['messages'][msg_index]
        except (ValueError, IndexError):
            await query.edit_message_text("Pesan tidak valid.")
            return

        token, error = await get_auth_token(email, password, provider)
        if error:
            await query.edit_message_text(f"Error: {error}")
            return

        content_pack, error = await fetch_message_content(
            token, provider, message_to_open['id'], base_url_hint=user_sessions[chat_id].get('base')
        )
        if error:
            await query.edit_message_text(f"Error: {error}")
            return

        if provider == "1secmail" and content_pack.get("base"):
            user_sessions[chat_id]['base'] = content_pack["base"]

        content = content_pack["item"]
        base_text = get_base_info_text(email, password, "Menampilkan isi pesan...")
        subject = content.get('subject', '(Tanpa subjek)')
        body = content.get('text', '(Tidak ada isi pesan teks)').strip()

        content_text = (
            f"\n\n┌─ *ISI PESAN* ─┐\n"
            f"│ *Subjek:* _{subject}_\n"
            f"└─" + "─"*20 + "─┘\n"
            f"`{body[:1500]}`"
        )
        response_text = base_text + content_text
        keyboard_list = [[InlineKeyboardButton("↩️ Kembali ke Inbox", callback_data="check_inbox_0")]]
        reply_markup = InlineKeyboardMarkup(keyboard_list)

    # --- Edit pesan aman ---
    if response_text and reply_markup:
        try:
            await query.edit_message_text(text=response_text, parse_mode='Markdown', reply_markup=reply_markup)
        except BadRequest as e:
            if "Message is not modified" in str(e):
                pass
            else:
                logger.error(f"Error BadRequest saat mengedit pesan: {e}")
        except Exception as e:
            logger.error(f"Error tak terduga saat mengedit pesan: {e}")

# --- MAIN ---
def main():
    print("\n" + "="*50 + "\n      BOT PEMBUAT EMAIL TELEGRAM OLEH NEZA\n" + "="*50)
    token = input("Masukkan Token Bot Telegram Anda di sini: ").strip()
    if not token:
        print("\n[!] KESALAHAN: Token tidak boleh kosong. Skrip berhenti.")
        return
    print("\n[✓] Token diterima. Menjalankan bot...\n" + "="*50)

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("buatemail", buat_email_command))
    application.add_handler(CallbackQueryHandler(button_callback_handler))

    print("\nBot sekarang online! Tekan CTRL+C untuk berhenti.")
    application.run_polling()

if __name__ == "__main__":
    main()
