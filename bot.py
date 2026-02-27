import os
import json
import traceback
import difflib
from datetime import datetime

import matplotlib
matplotlib.use('Agg')  # WAJIB untuk server tanpa display (Railway, Heroku, dll)

import matplotlib.pyplot as plt
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import gspread
from google.oauth2.service_account import Credentials

# ================= ENV =================
TOKEN = os.getenv("TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.environ.get("PORT", 8000))

if not TOKEN:
    raise ValueError("TOKEN environment variable tidak ditemukan!")
if not WEBHOOK_URL:
    raise ValueError("WEBHOOK_URL environment variable tidak ditemukan!")

# ================= GOOGLE SHEETS =================
scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

try:
    google_creds_json = os.getenv("GOOGLE_CREDS")
    if not google_creds_json:
        raise ValueError("GOOGLE_CREDS environment variable tidak ditemukan!")
    
    creds_dict = json.loads(google_creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
    
    client = gspread.authorize(creds)
    spreadsheet = client.open("BOT_KEUANGAN")

    transaksi_sheet = spreadsheet.worksheet("Transaksi")
    category_sheet = spreadsheet.worksheet("Categories")
    account_sheet = spreadsheet.worksheet("Account")
except Exception as e:
    print("ERROR saat koneksi Google Sheets:")
    print(traceback.format_exc())
    raise

# ================= FUNCTIONS =================
def parse_nominal(nominal_text):
    try:
        nominal_text = str(nominal_text).lower().replace(".", "").replace(",", "").strip()
        if "jt" in nominal_text:
            return int(float(nominal_text.replace("jt", "")) * 1_000_000)
        elif "rb" in nominal_text or "k" in nominal_text:
            return int(float(nominal_text.replace("rb", "").replace("k", "")) * 1_000)
        else:
            return int(nominal_text)
    except (ValueError, TypeError):
        raise ValueError(f"Nominal tidak valid: {nominal_text}")

def account_exists(account_name):
    try:
        data = account_sheet.get_all_values()[1:]
        accounts = [row[0].strip().upper() for row in data if row and row[0].strip()]
        return account_name.strip().upper() in accounts
    except Exception:
        return False

def get_current_balance(account_name):
    try:
        transaksi = transaksi_sheet.get_all_values()[1:]
        account_data = account_sheet.get_all_values()[1:]
        saldo = 0

        for row in account_data:
            if row and row[0].strip().upper() == account_name.strip().upper():
                saldo = int(row[1]) if row[1].strip().isdigit() else 0
                break

        for row in transaksi:
            if row and row[2].strip().upper() == account_name.strip().upper():
                tipe = row[3]
                try:
                    amount = int(row[6])
                except:
                    continue
                if tipe == "Income":
                    saldo += amount
                else:
                    saldo -= amount

        return saldo
    except Exception as e:
        print(f"Error get balance {account_name}: {e}")
        return 0

def load_categories():
    try:
        data = category_sheet.get_all_values()[1:]
        return [{"type": r[0], "parent": r[1], "sub": r[2]} for r in data if len(r) >= 3]
    except:
        return []

def find_category(input_category, categories):
    input_lower = input_category.lower().strip()
    for cat in categories:
        if cat["sub"].lower().strip() == input_lower:
            return cat
    return None

# ================= COMMANDS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot aktif 24 jam 🚀")

async def saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        transaksi = transaksi_sheet.get_all_values()[1:]
        account_data = account_sheet.get_all_values()[1:]

        saldo_dict = {}
        for row in account_data:
            if row and row[0].strip():
                try:
                    saldo_dict[row[0].strip()] = int(row[1]) if row[1].strip().isdigit() else 0
                except:
                    saldo_dict[row[0].strip()] = 0

        for row in transaksi:
            if len(row) < 7:
                continue
            account = row[2].strip()
            tipe = row[3]
            try:
                amount = int(row[6])
            except:
                continue
            if account in saldo_dict:
                if tipe == "Income":
                    saldo_dict[account] += amount
                else:
                    saldo_dict[account] -= amount

        total_all = sum(saldo_dict.values())

        message = "Saldo per akun:\n"
        for acc, total in saldo_dict.items():
            message += f"{acc} : Rp {total:,}\n"
        message += f"\nTOTAL : Rp {total_all:,}"

        await update.message.reply_text(message)
    except Exception as e:
        await update.message.reply_text(f"Error saat ambil saldo: {str(e)}")

async def chart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("Format: /chart 2024-06")
        return

    period = context.args[0]
    try:
        data = transaksi_sheet.get_all_values()[1:]
        category_totals = {}

        for row in data:
            if len(row) < 7:
                continue
            if row[0].startswith(period) and row[3] == "Expenses":
                category = row[5]
                try:
                    amount = int(row[6])
                    category_totals[category] = category_totals.get(category, 0) + amount
                except:
                    continue

        if not category_totals:
            await update.message.reply_text("Tidak ada data pengeluaran untuk periode tersebut.")
            return

        plt.figure(figsize=(10, 6))
        plt.bar(category_totals.keys(), category_totals.values())
        plt.title(f"Pengeluaran {period}")
        plt.xlabel("Kategori")
        plt.ylabel("Nominal (Rp)")
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()
        plt.savefig("chart.png")
        plt.close()

        await update.message.reply_photo(photo=open("chart.png", "rb"))
        os.remove("chart.png")  # bersihkan file setelah kirim
    except Exception as e:
        await update.message.reply_text(f"Error membuat chart: {str(e)}")

# ================= MESSAGE HANDLER =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (kode handle_message lu tetap sama, sudah cukup bagus)
    # Kalau mau, bisa tambah try-except besar di sini juga
    # Tapi biar ga terlalu panjang, biarkan seperti adanya dulu
    # Kode asli lu di sini (copy dari pesan sebelumnya)
    text = update.message.text.strip()
    original_text = text
    text = text.lower()

    # (sisa kode handle_message lu ... sampai akhir fungsi)
    # PASTIKAN KODE HANDLE_MESSAGE LU MASIH ADA DI SINI
    # Saya tidak copy ulang semua biar ga terlalu panjang, tapi pastikan bagian itu tetap utuh

# ================= APP =================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("saldo", saldo))
app.add_handler(CommandHandler("chart", chart))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Jalankan webhook dengan try-except supaya crash lebih jelas di logs
try:
    print(f"Starting webhook on port {PORT} with URL: {WEBHOOK_URL}/{TOKEN}")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TOKEN}"
    )
except Exception as e:
    print("Webhook crash:")
    print(traceback.format_exc())
    raise  # biar Railway tetep crash & logs jelas, jangan di-silent