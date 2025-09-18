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

# Inisialisasi Faker
fake = Faker('id_ID')

# Dictionary untuk menyimpan sesi email, password, mirror base, dan pesan per pengguna
user_sessions = {}

# =========================
# BACKEND: 1SECMAIL API (Mirror + Fallback 403)
# =========================

MIRRORS = [
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

# Domain publik 1secmail (bisa dipakai tanpa API create)
PUBLIC_DOMAINS = [
    "1secmail.com", "1secmail.net", "1secmail.org",
    "esiix.com", "wwjmp.com", "oosln.com", "vddaz.com",
    "xojxe.com", "yoggm.com", "zsero.com", "txcct.com"
]

async def _api_get(params: dict, base_url_hint: str | None = None):
    """
    GET ke 1secmail dengan mirror fallback & UA header.
    Return: (json, used_base_url, err_str)
    """
    mirrors = [base_url_hint] + MIRRORS if base_url_hint else MIRRORS
    last_err = "Tidak bisa menghubungi 1secmail (semua mirror)."
    for base in mirrors:
        try:
            async with httpx.AsyncClient(timeout=10, headers=UA_HEADERS, http2=True) as client:
                r = await client.get(base, params=params)
            if r.status_code == 200:
                return r.json(), base, None
            elif r.status_code in (401, 403):
                # Coba mirror lain
                last_err = f"HTTP {r.status_code} dari {base}"
                continue
            else:
                last_err = f"HTTP {r.status_code} dari {base}"
        except Exception as e:
            last_err = f"Koneksi error ke {base}: {e}"
    return None, None, last_err

def _make_local_login():
    """Buat login lokal ramah (lowercase + angka) sebagai fallback create."""
    return (
        f"{fake.first_name().lower()}{fake.last_name().lower()}{random.randint(10,99)}"
    ).replace(" ", "")

async def create_temp_email():
    """
    Buat email.
      1) Coba genRandomMailbox di semua mirror.
      2) Jika tetap 403/blok, buat alamat lokal (login@domainPublik) TANPA API.
    Password yang ditampilkan hanyalah dummy agar tampilan tetap sama.
    """
    # 1) coba genRandomMailbox
    data, used_base, err = await _api_get({"action": "genRandomMailbox", "count": 1})
    if not err and data:
        email_address = data[0]
        password = fake.password(length=12)  # dummy demi tampilan
        return {"email": email_address, "password": password, "base": used_base}, None

    # 2) fallback lokal
    login = _make_local_login()
    domain = random.choice(PUBLIC_DOMAINS)
    email_address = f"{login}@{domain}"
    password = fake.password(length=12)  # dummy
    # base None: nanti fetch_* akan coba semua mirror
    return {"email": email_address, "password": password, "base": None}, None

async def get_auth_token(email, password):
    """
    Tetap disediakan agar kompatibel dengan handler lama.
    1secmail tidak pakai token; kita kembalikan login & domain.
    """
    try:
        login, domain = email.split("@", 1)
        return {"login": login, "domain": domain}, None
    except ValueError:
        return None, "Format email tidak valid."

async def fetch_messages(token_like, base_url_hint: str | None = None):
    """
    Ambil daftar pesan; normalisasi struktur agar cocok dengan UI lama.
    Return: ({"items": [...], "base": used_base}, None) atau (None, err)
    """
    login = token_like["login"]
    domain = token_like["domain"]

    data, used_base, err = await _api_get(
        {"action": "getMessages", "login": login, "domain": domain},
        base_url_hint=base_url_hint
    )
    if err is not None and data is None:
        return None, "Gagal mengambil daftar pesan."

    messages = data or []
    normalized = []
    for m in messages:
        sender = m.get("from", "")
        normalized.append({
            "id": m.get("id"),
            "from": {"address": sender},
            "subject": m.get("subject", "(Tanpa subjek)"),
        })
    return {"items": normalized, "base": used_base}, None

async def fetch_message_content(token_like, message_id, base_url_hint: str | None = None):
    """
    Ambil isi pesan; mapping htmlBody/textBody ke 'text' agar UI lama tetap jalan.
    Return: ({"item": {...}, "base": used_base}, None) atau (None, err)
    """
    login = token_like["login"]
    domain = token_like["domain"]

    data, used_base, err = await _api_get(
        {"action": "readMessage", "login": login, "domain": domain, "id": message_id},
        base_url_hint=base_url_hint
    )
    if err or not data:
        return None, "Gagal mengambil isi pesan."

    subject = data.get("subject", "(Tanpa subjek)")
    text = (data.get("textBody") or data.get("body") or data.get("htmlBody") or "").strip()
    if not text and data.get("htmlBody"):
        text = data["htmlBody"]

    normalized = {"subject": subject, "text": text if text else "(Tidak ada isi pesan teks)"}
    return {"item": normalized, "base": used_base}, None

# =========================
# HANDLER TELEGRAM (TIDAK DIUBAH TAMPILAN)
# =========================

def get_base_info_text(email, password, footer_text):
    """Fungsi bantuan untuk membuat blok informasi akun dasar."""
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
        email, password = result['email'], result['password']
        user_sessions[chat_id] = {
            'email': email,
            'password': password,
            'base': result.get('base')  # simpan mirror yang berhasil kalau ada
        }

        keyboard = [[InlineKeyboardButton("📬 Cek Inbox", callback_data="check_inbox_0")]]
        response_text = get_base_info_text(email, password, "Gunakan tombol di bawah untuk memeriksa inbox.")

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
    email, password = session['email'], session['password']
    response_text, reply_markup = None, None

    # === AKSI: Cek Inbox ===
    if action == "check" and "inbox" in action_parts:
        token, error = await get_auth_token(email, password)
        if error:
            await query.edit_message_text(f"Error: {error}")
            return

        messages_pack, error = await fetch_messages(token, base_url_hint=user_sessions[chat_id].get('base'))
        if error:
            await query.edit_message_text(f"Error: {error}")
            return

        messages = messages_pack["items"]
        # simpan base yang sukses dipakai supaya konsisten
        if messages_pack.get("base"):
            user_sessions[chat_id]['base'] = messages_pack["base"]

        user_sessions[chat_id]['messages'] = messages

        base_text = get_base_info_text(email, password, "Inbox terakhir diperbarui...")
        inbox_text = "\n\n*Inbox Anda saat ini kosong.*"
        keyboard_list = [[InlineKeyboardButton(f"🔄 Refresh Inbox (0)", callback_data="check_inbox_0")]]

        if messages:
            inbox_text = "\n\n*Pesan yang diterima:*\n"
            keyboard_list = []
            for i, msg in enumerate(messages):
                sender = msg['from']['address']           # struktur sudah dinormalisasi
                subject = msg.get('subject', '(Tanpa subjek)')
                inbox_text += f"*{i+1}.* Dari: `{sender}`\n    Subjek: _{subject}_\n"
                keyboard_list.append([InlineKeyboardButton(f"✉️ Buka Pesan #{i+1}", callback_data=f"open_message_{i}")])
            keyboard_list.append([InlineKeyboardButton(f"🔄 Refresh Inbox ({len(messages)})", callback_data="check_inbox_0")])

        response_text = base_text + inbox_text
        reply_markup = InlineKeyboardMarkup(keyboard_list)

    # === AKSI: Buka Pesan ===
    elif action == "open" and "message" in action_parts:
        try:
            msg_index = int(action_parts[2])
            message_to_open = user_sessions[chat_id]['messages'][msg_index]
        except (ValueError, IndexError):
            await query.edit_message_text("Pesan tidak valid.")
            return

        token, error = await get_auth_token(email, password)
        if error:
            await query.edit_message_text(f"Error: {error}")
            return

        content_pack, error = await fetch_message_content(
            token,
            message_to_open['id'],
            base_url_hint=user_sessions[chat_id].get('base')
        )
        if error:
            await query.edit_message_text(f"Error: {error}")
            return

        # simpan base yang sukses
        if content_pack.get("base"):
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

    # --- PERBAIKAN ERROR: Coba edit pesan, jika tidak ada perubahan, abaikan ---
    if response_text and reply_markup:
        try:
            await query.edit_message_text(text=response_text, parse_mode='Markdown', reply_markup=reply_markup)
        except BadRequest as e:
            if "Message is not modified" in str(e):
                # abaikan
                pass
            else:
                logger.error(f"Error BadRequest saat mengedit pesan: {e}")
        except Exception as e:
            logger.error(f"Error tak terduga saat mengedit pesan: {e}")

# --- FUNGSI UTAMA UNTUK MENJALANKAN BOT ---
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
