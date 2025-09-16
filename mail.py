import logging
import httpx
import random
from faker import Faker
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

# Konfigurasi logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Inisialisasi Faker
fake = Faker('id_ID')

# Dictionary untuk menyimpan sesi email, password, dan pesan per pengguna
user_sessions = {}

# --- FUNGSI API MAIL.TM ---

async def get_mail_domain():
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("https://api.mail.tm/domains", timeout=10)
        if r.status_code == 200:
            return random.choice(r.json()['hydra:member'])['domain']
    except Exception as e:
        logger.error(f"Gagal mengambil domain: {e}")
    return None

async def create_temp_email():
    domain = await get_mail_domain()
    if not domain:
        return None, "Server mail.tm sedang tidak merespons."
    for _ in range(5):
        username = f"{fake.first_name().lower().replace(' ', '')}{fake.last_name().lower().replace(' ', '')}"
        email_address, password = f"{username}@{domain}", fake.password(length=12)
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post("https://api.mail.tm/accounts", json={"address": email_address, "password": password})
            if r.status_code == 201:
                return {"email": email_address, "password": password}, None
            elif r.status_code == 422:
                continue
        except Exception as e:
            logger.error(f"Error saat membuat email: {e}")
            return None, "Koneksi error saat membuat email."
    return None, "Gagal menemukan nama unik."

async def get_auth_token(email, password):
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post("https://api.mail.tm/token", json={"address": email, "password": password})
        if r.status_code == 200:
            return r.json()['token'], None
        return None, "Gagal login ke mail.tm (token tidak valid)."
    except Exception as e:
        logger.error(f"Error otentikasi: {e}")
        return None, "Koneksi error saat otentikasi."

async def fetch_messages(token):
    headers = {'Authorization': f'Bearer {token}'}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("https://api.mail.tm/messages", headers=headers)
        if r.status_code == 200:
            return r.json()['hydra:member'], None
        return None, "Gagal mengambil daftar pesan."
    except Exception as e:
        logger.error(f"Error mengambil pesan: {e}")
        return None, "Koneksi error saat mengambil pesan."

async def fetch_message_content(token, message_id):
    headers = {'Authorization': f'Bearer {token}'}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"https://api.mail.tm/messages/{message_id}", headers=headers)
        if r.status_code == 200:
            return r.json(), None
        return None, "Gagal mengambil isi pesan."
    except Exception as e:
        logger.error(f"Error mengambil isi pesan: {e}")
        return None, "Koneksi error saat mengambil isi pesan."

# --- HANDLER PERINTAH TELEGRAM ---

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

    # === AKSI: Cek Inbox ===
    if action == "check" and "inbox" in action_parts:
        token, error = await get_auth_token(email, password)
        if error: await query.edit_message_text(f"Error: {error}"); return
        
        messages, error = await fetch_messages(token)
        if error: await query.edit_message_text(f"Error: {error}"); return
        
        user_sessions[chat_id]['messages'] = messages
        
        base_text = get_base_info_text(email, password, "Inbox terakhir diperbarui...")
        inbox_text = "\n\n*Inbox Anda saat ini kosong.*"
        keyboard = [[InlineKeyboardButton(f"🔄 Refresh Inbox (0)", callback_data="check_inbox_0")]]
        
        if messages:
            inbox_text = "\n\n*Pesan yang diterima:*\n"
            keyboard = []
            for i, msg in enumerate(messages):
                sender = msg['from']['address']
                subject = msg.get('subject', '(Tanpa subjek)')
                inbox_text += f"*{i+1}.* Dari: `{sender}`\n    Subjek: _{subject}_\n"
                keyboard.append([InlineKeyboardButton(f"✉️ Buka Pesan #{i+1}", callback_data=f"open_message_{i}")])
            keyboard.append([InlineKeyboardButton(f"🔄 Refresh Inbox ({len(messages)})", callback_data="check_inbox_0")])
        
        response_text = base_text + inbox_text
        await query.edit_message_text(text=response_text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

    # === AKSI: Buka Pesan ===
    elif action == "open" and "message" in action_parts:
        try:
            msg_index = int(action_parts[2])
            message_to_open = user_sessions[chat_id]['messages'][msg_index]
        except (ValueError, IndexError):
            await query.edit_message_text("Pesan tidak valid."); return

        token, error = await get_auth_token(email, password)
        if error: await query.edit_message_text(f"Error: {error}"); return

        content, error = await fetch_message_content(token, message_to_open['id'])
        if error: await query.edit_message_text(f"Error: {error}"); return

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
        keyboard = [[InlineKeyboardButton("↩️ Kembali ke Inbox", callback_data="check_inbox_0")]]
        await query.edit_message_text(text=response_text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

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
