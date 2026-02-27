
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import difflib
import matplotlib.pyplot as plt
import os

# ================= ENV =================
TOKEN = os.getenv("TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.environ.get("PORT", 8000))

# ================= GOOGLE SHEETS =================
scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds = Credentials.from_service_account_info(
    json.loads(os.getenv("GOOGLE_CREDS"))
)

client = gspread.authorize(creds)
spreadsheet = client.open("BOT_KEUANGAN")

transaksi_sheet = spreadsheet.worksheet("Transaksi")
category_sheet = spreadsheet.worksheet("Categories")
account_sheet = spreadsheet.worksheet("Account")

# ================= FUNCTIONS =================
def parse_nominal(nominal_text):
    nominal_text = nominal_text.lower().replace(".", "").replace(",", "")
    if "jt" in nominal_text:
        return int(float(nominal_text.replace("jt", "")) * 1_000_000)
    elif "rb" in nominal_text or "k" in nominal_text:
        return int(float(nominal_text.replace("rb", "").replace("k", "")) * 1_000)
    else:
        return int(nominal_text)

def account_exists(account_name):
    data = account_sheet.get_all_values()[1:]
    accounts = [row[0].upper() for row in data]
    return account_name.upper() in accounts

def get_current_balance(account_name):
    transaksi = transaksi_sheet.get_all_values()[1:]
    account_data = account_sheet.get_all_values()[1:]
    saldo = 0

    for row in account_data:
        if row[0].upper() == account_name.upper():
            saldo = int(row[1])
            break

    for row in transaksi:
        if row[2].upper() == account_name.upper():
            tipe = row[3]
            amount = int(row[6])
            if tipe == "Income":
                saldo += amount
            else:
                saldo -= amount

    return saldo

def load_categories():
    data = category_sheet.get_all_values()[1:]
    return [{"type": r[0], "parent": r[1], "sub": r[2]} for r in data]

def find_category(input_category, categories):
    for cat in categories:
        if cat["sub"].lower() == input_category.lower():
            return cat
    return None

# ================= COMMANDS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot aktif 24 jam 🚀")

async def saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    transaksi = transaksi_sheet.get_all_values()[1:]
    account_data = account_sheet.get_all_values()[1:]

    saldo_dict = {row[0]: int(row[1]) for row in account_data}

    for row in transaksi:
        account = row[2]
        tipe = row[3]
        amount = int(row[6])
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

async def chart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("Format: /chart 2024-06")
        return

    period = context.args[0]
    data = transaksi_sheet.get_all_values()[1:]
    category_totals = {}

    for row in data:
        if row[0].startswith(period) and row[3] == "Expenses":
            category = row[5]
            amount = int(row[6])
            category_totals[category] = category_totals.get(category, 0) + amount

    if not category_totals:
        await update.message.reply_text("Tidak ada data.")
        return

    plt.figure()
    plt.bar(category_totals.keys(), category_totals.values())
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig("chart.png")
    plt.close()

    await update.message.reply_photo(photo=open("chart.png", "rb"))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    original_text = text  # simpan buat debug kalau perlu
    text = text.lower()

    # 1. Cek dulu kalau ini transfer (lebih robust)
    if "transfer" in text or "kirim" in text or "ke" in text:
        # Contoh valid: BCA 1jt transfer ke gopay buat bayar
        # atau: transfer 500rb dari mandiri ke dana
        parts = text.split()
        try:
            # Cari posisi nominal (cari yang bisa di-parse jadi angka)
            nominal_idx = None
            for i, p in enumerate(parts):
                try:
                    parse_nominal(p)
                    nominal_idx = i
                    break
                except:
                    pass

            if nominal_idx is None:
                await update.message.reply_text("Nominalnya mana bro? Contoh: 500rb")
                return

            nominal = parse_nominal(parts[nominal_idx])

            # Cari akun asal & tujuan
            from_acc = None
            to_acc = None

            # Case 1: AKUN nominal transfer/ke ke AKUN
            if nominal_idx > 0:
                from_acc_candidate = parts[nominal_idx-1].upper()
                if account_exists(from_acc_candidate):
                    from_acc = from_acc_candidate

            # Cari kata kunci tujuan
            ke_idx = -1
            for kw in ["ke", "transfer ke", "kirim ke", "tujuan"]:
                try:
                    ke_idx = parts.index(kw) if kw in parts else -1
                    if ke_idx != -1:
                        break
                except:
                    pass

            if ke_idx != -1 and ke_idx + 1 < len(parts):
                to_acc_candidate = parts[ke_idx + 1].upper()
                if account_exists(to_acc_candidate):
                    to_acc = to_acc_candidate

            # Kalau masih kurang, ambil dari sisa teks
            if from_acc is None and nominal_idx > 0:
                from_acc = parts[nominal_idx-1].upper()
            if to_acc is None and len(parts) > nominal_idx + 2:
                to_acc = parts[-1].upper()  # asumsi terakhir

            if not from_acc or not to_acc or not account_exists(from_acc) or not account_exists(to_acc):
                await update.message.reply_text(
                    "Format transfer kurang jelas. Contoh:\n"
                    "BCA 1jt ke GOPAY\n"
                    "transfer 500rb mandiri ke dana"
                )
                return

            if get_current_balance(from_acc) < nominal:
                await update.message.reply_text(f"Saldo {from_acc} kurang bro (Rp {get_current_balance(from_acc):,}) ❌")
                return

            tanggal = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            nama = update.message.from_user.first_name

            transaksi_sheet.append_row([tanggal, nama, from_acc, "Expenses", "Financial", "Transfer", nominal, f"Transfer ke {to_acc}"])
            transaksi_sheet.append_row([tanggal, nama, to_acc, "Income", "Financial", "Transfer", nominal, f"Transfer dari {from_acc}"])

            await update.message.reply_text(f"Transfer {nominal:,} dari {from_acc} ke {to_acc} berhasil ✅")
            return

        except Exception as e:
            await update.message.reply_text(f"Error pas parse transfer: {str(e)}\nCoba ketik ulang ya")
            return

    # 2. Mode biasa: pengeluaran/pemasukan
    # Contoh valid:
    # BCA 450rb makan warteg
    # GOPAY 50rb income gaji
    # 200rb BCA belanja
    parts = text.split()
    if len(parts) < 2:
        return

    # Cari nominal dulu (yang bisa di-parse)
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
        await update.message.reply_text("Nominalnya ga kebaca. Contoh: 500rb atau 1jt")
        return

    categories = load_categories()

    # Sisanya dianggap akun + kategori + deskripsi
    remaining = parts[:nominal_idx] + parts[nominal_idx+1:]

    # Asumsi: akun biasanya 1 kata di awal atau akhir
    possible_accounts = [p.upper() for p in remaining if account_exists(p.upper())]

    if not possible_accounts:
        await update.message.reply_text("Akunnya ga ketemu. Pastiin akun udah ada di sheet Account")
        return

    # Ambil akun pertama yang ketemu (atau minta klarifikasi kalau >1)
    account = possible_accounts[0]

    # Sisanya jadi kategori + deskripsi
    cat_part = " ".join([p for p in remaining if p.upper() != account])
    cat_words = cat_part.split()
    if not cat_words:
        await update.message.reply_text("Kategori mana bro?")
        return

    # Coba cari sub-kategori yang cocok (pake fuzzy matching biar lebih toleran)
    best_cat = None
    best_score = 0.0
    for cat in categories:
        sub_lower = cat["sub"].lower()
        for word in cat_words:
            score = difflib.SequenceMatcher(None, word, sub_lower).ratio()
            if score > best_score and score > 0.6:  # threshold 60%
                best_score = score
                best_cat = cat

    if best_cat is None:
        # Coba gabung 2 kata terakhir sebagai kategori
        if len(cat_words) >= 2:
            combined = " ".join(cat_words[-2:])
            for cat in categories:
                if combined in cat["sub"].lower():
                    best_cat = cat
                    break

    if best_cat is None:
        await update.message.reply_text(
            f"Kategori '{cat_part}' ga ketemu. Cek di sheet Categories ya\n"
            f"Contoh: makan, transport, gaji, dll"
        )
        return

    # Deskripsi = sisa kata selain akun & kategori
    description_words = [w for w in cat_words if w not in best_cat["sub"].lower()]
    description = " ".join(description_words) if description_words else "-"

    # Cek saldo kalau expense
    if best_cat["type"] == "Expenses" and get_current_balance(account) < nominal:
        await update.message.reply_text(f"Saldo {account} kurang (Rp {get_current_balance(account):,}) ❌")
        return

    tanggal = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    nama = update.message.from_user.first_name

    transaksi_sheet.append_row([
        tanggal, nama, account, best_cat["type"],
        best_cat["parent"], best_cat["sub"], nominal, description
    ])

    await update.message.reply_text(
        f"OK ✓\n{account} | {nominal:,} | {best_cat['sub']} | {description}"
    )

# ================= APP =================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("saldo", saldo))
app.add_handler(CommandHandler("chart", chart))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

app.run_webhook(
    listen="0.0.0.0",
    port=PORT,
    url_path=TOKEN,
    webhook_url=f"{WEBHOOK_URL}/{TOKEN}"
)