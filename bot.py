import sys  # <-- TAMBAH BARIS INI
import os
import json
import traceback
import difflib
from datetime import datetime

# (sisa import lu seperti matplotlib.use('Agg'), telegram, dll)

print("=== BOT MULAI JALAN DI RAILWAY ===")
print("Python version:", sys.version)
print("Current working dir:", os.getcwd())
print("Env vars available:", list(os.environ.keys())[:10])  # 10 env pertama
print("TOKEN ada?", "TOKEN" in os.environ)
print("GOOGLE_CREDS ada?", "GOOGLE_CREDS" in os.environ)
print("WEBHOOK_URL ada?", "WEBHOOK_URL" in os.environ)
print("PORT from env:", os.environ.get("PORT", "tidak ada"))

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
PORT = int(os.environ.get("PORT", 8080))

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
    try:
        text = update.message.text.strip()
        original_text = text
        text_lower = text.lower()

        # Debug: konfirmasi pesan masuk
        print(f"DEBUG: Pesan masuk: '{text}' dari {update.message.from_user.first_name}")

        # 0. Pesan non-transaksi → balas ramah
        if len(text_lower.split()) <= 1 or not any(w in text_lower for w in ["rb", "jt", "k", "000", "transfer", "kirim", "ke", "income"]):
            await update.message.reply_text(
                "Halo bro! Mau catat apa nih?\n"
                "Contoh transaksi:\n"
                "• BCA 50rb makan\n"
                "• gopay 1jt gaji\n"
                "• transfer bca 200rb ke dana\n"
                "\nAtau coba /saldo /chart"
            )
            return

        categories = load_categories()
        if not categories:
            await update.message.reply_text("Kategori ga bisa di-load bro, cek sheet Categories ya")
            return

        # 1. Deteksi transfer dulu (lebih prioritas)
        if any(kw in text_lower for kw in ["transfer", "kirim", "ke"]):
            parts = text_lower.split()
            nominal = None
            nominal_idx = -1
            for i, p in enumerate(parts):
                try:
                    nominal = parse_nominal(p)
                    nominal_idx = i
                    break
                except:
                    continue

            if nominal is None:
                await update.message.reply_text("Nominal transfernya mana bro? Contoh: transfer bca 500rb ke gopay")
                return

            # Cari akun asal & tujuan
            from_acc = None
            to_acc = None

            # Cari kata kunci tujuan
            ke_idx = -1
            for kw in ["ke", "ke ", "kirim ke", "transfer ke", "tujuan"]:
                if kw in text_lower:
                    ke_idx = text_lower.find(kw) // len(parts[0])  # approx index
                    break

            if ke_idx != -1 and nominal_idx < len(parts) - 1:
                to_candidate = parts[-1].upper()
                if account_exists(to_candidate):
                    to_acc = to_candidate

            # Akun asal biasanya sebelum nominal
            if nominal_idx > 0:
                from_candidate = parts[nominal_idx - 1].upper()
                if account_exists(from_candidate):
                    from_acc = from_candidate

            # Fallback ambil dua akun yang mungkin
            possible_accounts = [p.upper() for p in parts if account_exists(p.upper())]
            if len(possible_accounts) >= 2:
                from_acc = possible_accounts[0]
                to_acc = possible_accounts[1]

            if not from_acc or not to_acc:
                await update.message.reply_text(
                    f"Ga nemu akunnya bro. Pastiin {from_acc or 'asal'} dan {to_acc or 'tujuan'} ada di sheet Account.\n"
                    "Contoh: transfer BCA 500rb ke GOPAY"
                )
                return

            if get_current_balance(from_acc) < nominal:
                await update.message.reply_text(f"Saldo {from_acc} kurang bro (Rp {get_current_balance(from_acc):,}) ❌")
                return

            tanggal = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            nama = update.message.from_user.first_name

            transaksi_sheet.append_row([tanggal, nama, from_acc, "Expenses", "Financial", "Transfer", nominal, f"Transfer ke {to_acc}"])
            transaksi_sheet.append_row([tanggal, nama, to_acc, "Income", "Financial", "Transfer", nominal, f"Transfer dari {from_acc}"])

            await update.message.reply_text(
                f"Transfer sukses bro! 💸\n"
                f"{nominal:,} dari {from_acc} ke {to_acc}\n"
                f"Saldo {from_acc} sekarang: Rp {get_current_balance(from_acc):,}"
            )
            return

        # 2. Mode transaksi biasa (pengeluaran / pemasukan)
        parts = text_lower.split()
        nominal = None
        nominal_idx = -1
        for i, p in enumerate(parts):
            try:
                nominal = parse_nominal(p)
                nominal_idx = i
                break
            except:
                continue

        if nominal is None:
            await update.message.reply_text("Nominal ga kebaca bro. Contoh: 500rb atau 1jt")
            return

        # Akun: cari yang match di sheet Account
        possible_accounts = [p.upper() for p in parts if account_exists(p.upper())]
        if not possible_accounts:
            await update.message.reply_text("Akun ga ketemu. Pastiin nama akun sama persis di sheet Account")
            return

        account = possible_accounts[0]  # ambil pertama, kalau banyak bisa tambah klarifikasi nanti

        # Kategori: fuzzy match + gabung 2-3 kata kalau perlu
        remaining_text = " ".join(parts[:nominal_idx] + parts[nominal_idx+1:])
        remaining_words = remaining_text.split()

        best_cat = None
        best_score = 0.0
        for cat in categories:
            sub_lower = cat["sub"].lower()
            # Exact match atau fuzzy per kata
            for word in remaining_words:
                score = difflib.SequenceMatcher(None, word, sub_lower).ratio()
                if score > best_score and score > 0.65:  # threshold lebih longgar
                    best_score = score
                    best_cat = cat
            # Coba gabung 2-3 kata terakhir
            if len(remaining_words) >= 2:
                combined2 = " ".join(remaining_words[-2:])
                if combined2 in sub_lower:
                    best_cat = cat
                    break

        if best_cat is None:
            await update.message.reply_text(
                f"Kategori '{remaining_text}' ga ketemu bro.\n"
                f"Cek sheet Categories atau coba kata kunci seperti: makan, transport, gaji, belanja"
            )
            return

        # Deskripsi: sisa kata selain akun & nominal & kategori
        description_parts = [w for w in remaining_words if w not in best_cat["sub"].lower() and w != account.lower()]
        description = " ".join(description_parts).strip() or "-"

        # Cek saldo kalau expense
        if best_cat["type"] == "Expenses" and get_current_balance(account) < nominal:
            await update.message.reply_text(f"Saldo {account} kurang bro (Rp {get_current_balance(account):,}) ❌")
            return

        tanggal = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        nama = update.message.from_user.first_name

        transaksi_sheet.append_row([
            tanggal, nama, account, best_cat["type"],
            best_cat["parent"], best_cat["sub"], nominal, description or original_text
        ])

        reply = f"OK ✓ Tercatat!\n" \
                f"Akun: {account}\n" \
                f"Nominal: Rp {nominal:,}\n" \
                f"Kategori: {best_cat['sub']} ({best_cat['type']})\n" \
                f"Deskripsi: {description or '-'}\n" \
                f"Saldo {account} sekarang: Rp {get_current_balance(account):,}"
        
        await update.message.reply_text(reply)

    except Exception as e:
        print(f"ERROR di handle_message: {str(e)}")
        print(traceback.format_exc())
        await update.message.reply_text(f"Waduh error bro: {str(e)}\nCoba ketik ulang atau /start dulu ya")
        
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