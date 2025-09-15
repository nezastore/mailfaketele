import logging
import httpx  # Menggantikan library requests
import random
from faker import Faker
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Konfigurasi logging untuk melihat error (opsional tapi sangat disarankan)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Inisialisasi Faker untuk membuat nama acak
fake = Faker('id_ID')  # Menggunakan lokal Indonesia untuk nama yang lebih familiar

# --- FUNGSI UTAMA PEMBUAT EMAIL ---

async def get_mail_domain():
    """Mengambil domain yang tersedia dari API mail.tm secara asinkron."""
    try:
        # Menggunakan httpx untuk request asinkron
        async with httpx.AsyncClient() as client:
            response = await client.get("https://api.mail.tm/domains", timeout=10)
        
        if response.status_code == 200:
            domains = response.json()['hydra:member']
            return random.choice(domains)['domain']
    except Exception as e:
        logger.error(f"Gagal mengambil domain mail.tm: {e}")
        return None
    return None

async def create_temp_email():
    """Membuat akun email sementara dengan nama orang acak."""
    domain = await get_mail_domain()
    if not domain:
        return None, "Server mail.tm sedang tidak merespons. Coba lagi nanti."

    for i in range(5):  # Mencoba membuat nama unik hingga 5 kali
        # Membuat username dari nama acak
        first_name = fake.first_name().lower().replace(" ", "")
        last_name = fake.last_name().lower().replace(" ", "")
        username = f"{first_name}.{last_name}"
        if i > 0:  # Jika nama sudah terpakai, tambahkan angka acak
            username += str(random.randint(10, 999))
        
        email_address = f"{username}@{domain}"
        password = fake.password(length=12, special_chars=True, digits=True, upper_case=True, lower_case=True)
        
        try:
            # Mengirim permintaan untuk membuat akun menggunakan httpx
            api_url = "https://api.mail.tm/accounts"
            headers = {"Content-Type": "application/json"}
            data = {"address": email_address, "password": password}
            
            async with httpx.AsyncClient() as client:
                response = await client.post(api_url, headers=headers, json=data, timeout=10)
            
            if response.status_code == 201:  # 201 Created = Sukses
                return {"email": email_address, "password": password}, None
            elif response.status_code == 422: # Jika username sudah terpakai
                logger.warning(f"Username '{username}' sudah terpakai, mencoba nama lain...")
                continue
            else:
                error_detail = response.json().get('hydra:description', 'Error tidak diketahui')
                return None, f"Gagal membuat akun. Server: {error_detail} (Status: {response.status_code})"
                
        except Exception as e:
            logger.error(f"Error saat membuat email: {e}")
            return None, "Terjadi kesalahan koneksi saat membuat email."
            
    return None, "Gagal menemukan nama unik setelah beberapa kali percobaan."

# --- HANDLER PERINTAH TELEGRAM ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk perintah /start."""
    user_name = update.message.from_user.first_name
    welcome_message = (
        f"👋 Halo, *{user_name}*!\n\n"
        "Selamat datang di Bot Pembuat Email Sementara.\n\n"
        "Kirim perintah /buatemail untuk mendapatkan alamat email dan password baru secara instan.\n\n"
        "Bot ini dibuat oleh *Neza*."
    )
    await update.message.reply_text(welcome_message, parse_mode='Markdown')

async def buat_email_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk perintah /buatemail."""
    processing_message = await update.message.reply_text("Sedang memproses permintaan Anda, mohon tunggu... ⏳")
    
    result, error_message = await create_temp_email()
    
    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=processing_message.message_id)
    
    if result:
        email = result['email']
        password = result['password']
        
        response_text = (
            "✅ *Email Berhasil Dibuat* ✅\n\n"
            "Berikut adalah detail akun email sementara Anda:\n\n"
            "📧 *Alamat Email:*\n"
            f"`{email}`\n\n"
            "🔑 *Password:*\n"
            f"`{password}`\n\n"
            "Anda bisa login dan memeriksa inbox di situs [mail.tm](https://mail.tm/)."
        )
        await update.message.reply_text(response_text, parse_mode='Markdown', disable_web_page_preview=True)
    else:
        error_text = f"❌ *Gagal Membuat Email*\n\n*Alasan:* {error_message}"
        await update.message.reply_text(error_text, parse_mode='Markdown')

# --- FUNGSI UTAMA UNTUK MENJALANKAN BOT ---

def main():
    """Fungsi utama untuk menjalankan bot."""
    
    # --- Kolom Input Token yang Jelas ---
    print("\n" + "="*50)
    print("      BOT PEMBUAT EMAIL TELEGRAM OLEH NEZA")
    print("="*50)
    print("\nSilakan masukkan Token Bot Anda.")
    print("Anda bisa mendapatkannya dari @BotFather di Telegram.")
    
    token = input("Masukkan Token di sini: ").strip()
    
    if not token:
        print("\n[!] KESALAHAN: Token tidak boleh kosong. Skrip berhenti.")
        return
    print("\n[✓] Token diterima. Mencoba menjalankan bot...")
    print("="*50)
    # ------------------------------------

    application = Application.builder().token(token).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("buatemail", buat_email_command))
    
    print("\nBot sekarang online dan berjalan!")
    print("Tekan CTRL+C untuk menghentikan bot.")
    application.run_polling()

if __name__ == "__main__":
    main()
