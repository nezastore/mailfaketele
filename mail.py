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

# Dictionary untuk menyimpan sesi email & password per pengguna (berbasis chat_id)
# NOTE: Data ini akan hilang jika skrip di-restart. Untuk produksi, gunakan database.
user_sessions = {}

# --- FUNGSI API MAIL.TM ---

async def get_mail_domain():
    """Mengambil domain yang tersedia dari API mail.tm."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("https://api.mail.tm/domains", timeout=10)
        if response.status_code == 200:
            return random.choice(response.json()['hydra:member'])['domain']
    except Exception as e:
        logger.error(f"Gagal mengambil domain: {e}")
    return None

async def create_temp_email():
    """Membuat akun email sementara."""
    domain = await get_mail_domain()
    if not domain:
        return None, "Server mail.tm sedang tidak merespons."

    for _ in range(5):
        first_name = fake.first_name().lower().replace(" ", "")
        last_name = fake.last_name().lower().replace(" ", "")
        username = f"{first_name}{last_name}"
        email_address = f"{username}@{domain}"
        password = fake.password(length=12)
        
        try:
            data = {"address": email_address, "password": password}
            async with httpx.AsyncClient() as client:
                response = await client.post("https://api.mail.tm/accounts", json=data, timeout=10)
            if response.status_code == 201:
                return {"email": email_address, "password": password}, None
            elif response.status_code == 422:
                logger.warning(f"Username '{username}' terpakai, mencoba lagi...")
                continue
            else:
                return None, f"Gagal membuat akun. Status: {response.status_code}"
        except Exception as e:
            logger.error(f"Error saat membuat email: {e}")
            return None, "Koneksi error saat membuat email."
    return None, "Gagal menemukan nama unik."

async def fetch_messages(email, password):
    """Mengambil pesan dari inbox sebuah akun email."""
    try:
        # 1. Dapatkan token otorisasi
        async with httpx.AsyncClient() as client:
            auth_response = await client.post("https://api.mail.tm/token", json={"address": email, "password": password})
            if auth_response.status_code != 200:
                return None, "Gagal login ke mail.tm (username/password salah)."
            
            token = auth_response.json()['token']
            headers = {'Authorization': f'Bearer {token}'}
            
            # 2. Ambil daftar pesan
            messages_response = await client.get("https://api.mail.tm/messages", headers=headers)
            if messages_response.status_code == 200:
                return messages_response.json()['hydra:member'], None
            else:
                return None, "Gagal mengambil pesan dari server."
    except Exception as e:
        logger.error(f"Error saat mengambil pesan: {e}")
        return None, "Koneksi error saat mengambil pesan."


# --- HANDLER PERINTAH TELEGRAM ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk perintah /start."""
    user_name = update.message.from_user.first_name
    await update.message.reply_text(
        f"👋 Halo, *{user_name}*!\n\n"
        "Kirim /buatemail untuk membuat email baru.\n"
        "Setelah email dibuat, Anda akan mendapatkan tombol untuk memeriksa inbox.",
        parse_mode='Markdown'
    )

async def buat_email_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Membuat email baru dan menampilkan tombol Cek Inbox."""
    chat_id = update.effective_chat.id
    processing_message = await update.message.reply_text("⏳ Sedang membuat akun email Anda...")
    
    result, error_message = await create_temp_email()
    
    await context.bot.delete_message(chat_id=chat_id, message_id=processing_message.message_id)
    
    if result:
        email = result['email']
        password = result['password']
        # Simpan sesi pengguna
        user_sessions[chat_id] = {'email': email, 'password': password}
        
        keyboard = [[InlineKeyboardButton("📬 Cek Inbox (0)", callback_data="check_inbox")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        response_text = (
            f"┌─  *AKUN EMAIL ANDA TELAH SIAP* ─┐\n"
            f"│\n"
            f"│  📧  *Email*\n"
            f"│  `{email}`\n"
            f"│\n"
            f"│  🔑  *Password*\n"
            f"│  `{password}`\n"
            f"│\n"
            f"└─  *Gunakan tombol di bawah untuk memeriksa inbox.* ─┘"
        )
        await update.message.reply_text(response_text, parse_mode='Markdown', reply_markup=reply_markup)
    else:
        await update.message.reply_text(f"❌ *Gagal Membuat Email*\n\n*Alasan:* {error_message}", parse_mode='Markdown')

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menangani semua klik tombol inline."""
    query = update.callback_query
    await query.answer("Sedang memeriksa inbox...") # Tampilkan notifikasi loading singkat
    
    chat_id = query.effective_chat.id
    
    if query.data == "check_inbox":
        session = user_sessions.get(chat_id)
        if not session:
            await query.edit_message_text("Sesi Anda tidak ditemukan. Silakan buat email baru dengan /buatemail.", reply_markup=None)
            return

        email = session['email']
        password = session['password']
        
        messages, error = await fetch_messages(email, password)
        
        if error:
            await query.edit_message_text(f"Terjadi kesalahan: {error}", reply_markup=query.message.reply_markup)
            return

        # Format pesan untuk ditampilkan di Telegram
        if not messages:
            inbox_text = "\n*Inbox Anda saat ini kosong.*"
        else:
            inbox_text = "\n*Pesan yang diterima:*\n"
            for msg in messages:
                sender = msg['from']['address']
                subject = msg.get('subject', '(Tanpa subjek)')
                inbox_text += f"• Dari: `{sender}`\n  Subjek: _{subject}_\n"
        
        # Susun ulang pesan asli dengan info inbox
        response_text = (
            f"┌─  *AKUN EMAIL ANDA* ─┐\n"
            f"│\n"
            f"│  📧  *Email*\n"
            f"│  `{email}`\n"
            f"│\n"
            f"│  🔑  *Password*\n"
            f"│  `{password}`\n"
            f"│\n"
            f"└─  *Inbox terakhir diperbarui...* ─┘"
            f"{inbox_text}"
        )
        
        # Perbarui tombol dengan jumlah pesan baru
        keyboard = [[InlineKeyboardButton(f"📬 Cek Ulang Inbox ({len(messages)})", callback_data="check_inbox")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(text=response_text, parse_mode='Markdown', reply_markup=reply_markup)


# --- FUNGSI UTAMA UNTUK MENJALANKAN BOT ---
def main():
    """Fungsi utama untuk menjalankan bot."""
    print("\n" + "="*50)
    print("      BOT PEMBUAT EMAIL TELEGRAM OLEH NEZA")
    print("="*50)
    token = input("Masukkan Token Bot Telegram Anda di sini: ").strip()
    if not token:
        print("\n[!] KESALAHAN: Token tidak boleh kosong. Skrip berhenti.")
        return
    print("\n[✓] Token diterima. Menjalankan bot...")
    print("="*50)

    application = Application.builder().token(token).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("buatemail", buat_email_command))
    application.add_handler(CallbackQueryHandler(button_callback_handler)) # Handler untuk tombol
    
    print("\nBot sekarang online! Tekan CTRL+C untuk berhenti.")
    application.run_polling()

if __name__ == "__main__":
    main()
