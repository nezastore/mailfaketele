import logging
import httpx
import random
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

# Dictionary untuk menyimpan sesi email, password (dummy), dan pesan per pengguna
user_sessions = {}

# =========================
# BACKEND: 1SECMAIL API
# =========================
BASE_URL = "https://www.1secmail.com/api/v1/"

async def _api_get(params: dict):
    """Helper untuk GET ke 1secmail dengan timeout & error handling seragam."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(BASE_URL, params=params)
        if r.status_code == 200:
            return r.json(), None
        return None, f"HTTP {r.status_code} dari 1secmail."
    except Exception as e:
        logger.error(f"HTTP error 1secmail: {e}")
        return None, "Koneksi error ke 1secmail."

async def create_temp_email():
    """
    Ganti pembuatan akun dari mail.tm -> 1secmail.
    1secmail tidak butuh password, tapi kita tetap membuat password dummy
    agar TAMPILAN tetap sama seperti sebelumnya.
    """
    # Buat 1 alamat random
    data, err = await _api_get({"action": "genRandomMailbox", "count": 1})
    if err or not data:
        return None, (err or "Gagal membuat email dari 1secmail.")
    try:
        email_address = data[0]
    except Exception:
        return None, "Response 1secmail tidak sesuai."

    # Password dummy hanya untuk ditampilkan (tidak dipakai otentikasi)
    password = fake.password(length=12)

    return {"email": email_address, "password": password}, None

async def get_auth_token(email, password):
    """
    DISET tetap ada agar handler lama tidak berubah.
    1secmail tidak pakai token; kita kembalikan dict 'kredensial' berisi login & domain.
    """
    try:
        login, domain = email.split("@", 1)
        return {"login": login, "domain": domain}, None
    except ValueError:
        return None, "Format email tidak valid."

async def fetch_messages(token_like):
    """
    Ambil daftar pesan dari 1secmail dan NORMALISASI struktur agar sama
    dengan yang dipakai UI lama:
      - 'from' -> {'address': "..."}
      - subject tetap
      - id tetap
    """
    login = token_like["login"]
    domain = token_like["domain"]

    data, err = await _api_get({
        "action": "getMessages",
        "login": login,
        "domain": domain
    })
    if err is not None:
        return None, "Gagal mengambil daftar pesan."

    messages = data or []
    # Normalisasi struktur agar kompatibel dengan UI lama
    normalized = []
    for m in messages:
        # 1secmail memulangkan: id, from, subject, date, attachments
        sender = m.get("from", "")
        normalized.append({
            "id": m.get("id"),
            "from": {"address": sender},
            "subject": m.get("subject", "(Tanpa subjek)"),
        })
    return normalized, None

async def fetch_message_content(token_like, message_id):
    """
    Ambil isi pesan. 1secmail punya 'textBody' & 'htmlBody'.
    UI lama membaca key 'text', jadi kita mapping ke 'text'.
    """
    login = token_like["login"]
    domain = token_like["domain"]

    data, err = await _api_get({
        "action": "readMessage",
        "login": login,
        "domain": domain,
        "id": message_id
    })
    if err or not data:
        return None, "Gagal mengambil isi pesan."

    # Normalisasi agar handler lama tidak berubah
    subject = data.get("subject", "(Tanpa subjek)")
    text = (data.get("textBody") or data.get("body") or data.get("htmlBody") or "").strip()
    # Jika htmlBody ada tapi textBody kosong, kita ambil sebagian html sebagai fallback
    if not text and data.get("htmlBody"):
        text = data["htmlBody"]

    normalized = {
        "subject": subject,
        "text": text if text else "(Tidak ada isi pesan teks)"
    }
    return normalized, None

# =========================
# HANDLER TELEGRAM (TIDAK DIUBAH)
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
        user_sessions[chat_id] = {'email': email, 'password': password}

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

        messages, error = await fetch_messages(token)
        if error:
            await query.edit_message_text(f"Error: {error}")
            return

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

        content, error = await fetch_message_content(token, message_to_open['id'])
        if error:
            await query.edit_message_text(f"Error: {error}")
            return

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
