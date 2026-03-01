import sys
import os
import json
import traceback
import difflib
from datetime import datetime
import asyncio

print("=== BOT MULAI JALAN DI RAILWAY ===")
print("Python version:", sys.version)
print("Current working dir:", os.getcwd())
print("Env vars available:", list(os.environ.keys())[:10])
print("TOKEN ada?", "TOKEN" in os.environ)
print("GOOGLE_CREDS ada?", "GOOGLE_CREDS" in os.environ)
print("WEBHOOK_URL ada?", "WEBHOOK_URL" in os.environ)
print("PORT from env:", os.environ.get("PORT", "tidak ada"))

import matplotlib
matplotlib.use('Agg')

import matplotlib.pyplot as plt
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

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

# ================= PRIVASI & USER MANAGEMENT =================
OWNER_ID = 6901833402  # GANTI DENGAN ID TELEGRAM LU (dari @userinfobot)
ALLOWED_USER_IDS = set()  # akan di-load dari sheet

async def load_allowed_users():
    global ALLOWED_USER_IDS
    try:
        user_sheet = spreadsheet.worksheet("USER")
        user_data = user_sheet.get_all_values()[1:]  # skip header
        allowed = set()
        for row in user_data:
            if len(row) >= 1 and row[0].strip().isdigit():
                user_id = int(row[0].strip())
                status = row[2].strip().lower() if len(row) > 2 else "active"
                if status == "active":
                    allowed.add(user_id)
        ALLOWED_USER_IDS = allowed
        print(f"DEBUG: Loaded {len(allowed)} user allowed dari sheet USER")
        return True
    except gspread.exceptions.WorksheetNotFound:
        print("WARNING: Sheet 'USER' tidak ditemukan. Bot jadi public sementara.")
        ALLOWED_USER_IDS = set()
        return False
    except Exception as e:
        print(f"ERROR load allowed users: {e}")
        return False

async def is_allowed_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USER_IDS:
        await update.message.reply_text("Maaf bro, bot ini privat. Hanya user terdaftar yang bisa pakai.")
        print(f"DEBUG: User ditolak: ID {user_id} ({update.effective_user.username or 'no username'})")
        return False
    return True

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
    if not await is_allowed_user(update, context):
        return
    await update.message.reply_text("Bot aktif 24 jam 🚀 Selamat datang bro!")

async def saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed_user(update, context):
        return
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
    if not await is_allowed_user(update, context):
        return
    if len(context.args) < 1:
        await update.message.reply_text(
            "Format pro chart:\n"
            "/chart [periode] [tipe] [filter]\n\n"
            "Contoh:\n"
            "• /chart 2025-02 → bar pengeluaran Feb 2025\n"
            "• /chart 2025-02 pie → pie chart Feb\n"
            "• /chart 2025 line → trend bulanan 2025\n"
            "• /chart all expenses → semua pengeluaran\n"
            "• /chart income 2025 → pemasukan 2025"
        )
        return

    args = context.args
    period = args[0].lower()
    chart_type = 'bar'  # default
    data_filter = 'expenses'  # default

    if len(args) > 1:
        if args[1] in ['bar', 'pie', 'line']:
            chart_type = args[1]
        elif args[1] in ['expenses', 'income', 'all']:
            data_filter = args[1]

    if len(args) > 2:
        if args[2] in ['expenses', 'income', 'all']:
            data_filter = args[2]

    try:
        data = transaksi_sheet.get_all_values()[1:]  # skip header
        if not data:
            await update.message.reply_text("Belum ada data transaksi bro")
            return

        category_totals = {}
        monthly_trend = {}  # untuk line chart

        for row in data:
            if len(row) < 7:
                continue
            date_str = row[0]
            tipe = row[3]
            category = row[5]
            try:
                amount = int(row[6])
            except:
                continue

            # Filter data sesuai request
            if data_filter == 'expenses' and tipe != "Expenses":
                continue
            if data_filter == 'income' and tipe != "Income":
                continue

            # Proses periode
            if period == 'all':
                pass
            elif '-' in period:  # YYYY-MM
                if not date_str.startswith(period):
                    continue
            elif len(period) == 4 and period.isdigit():  # YYYY
                if not date_str.startswith(period):
                    continue
            else:
                await update.message.reply_text("Format periode salah. Gunakan YYYY-MM atau YYYY atau 'all'")
                return

            # Hitung total per kategori
            category_totals[category] = category_totals.get(category, 0) + amount

            # Trend bulanan untuk line chart
            if chart_type == 'line':
                month_key = date_str[:7]  # YYYY-MM
                monthly_trend[month_key] = monthly_trend.get(month_key, 0) + amount

        if not category_totals and chart_type != 'line':
            await update.message.reply_text(f"Tidak ada data {data_filter} untuk periode '{period}'")
            return

        if chart_type == 'line' and not monthly_trend:
            await update.message.reply_text(f"Tidak ada data trend untuk periode '{period}'")
            return

        # Warna custom per kategori (bisa ditambah sesuai sheet Categories)
        colors = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#FFEEAD', '#D4A5A5', '#9B59B6', '#3498DB']

        plt.figure(figsize=(12, 7))

        if chart_type == 'pie':
            plt.pie(
                category_totals.values(),
                labels=category_totals.keys(),
                autopct='%1.1f%%',
                colors=colors[:len(category_totals)],
                startangle=90,
                shadow=True
            )
            plt.title(f"Distribusi {data_filter.capitalize()} - {period.upper()}")
            plt.axis('equal')

        elif chart_type == 'line':
            sorted_months = sorted(monthly_trend.keys())
            values = [monthly_trend[m] for m in sorted_months]
            plt.plot(sorted_months, values, marker='o', linewidth=2, color='#1abc9c')
            plt.fill_between(sorted_months, values, alpha=0.2, color='#1abc9c')
            plt.title(f"Trend {data_filter.capitalize()} - {period.upper()}")
            plt.xlabel("Bulan")
            plt.ylabel("Nominal (Rp)")
            plt.xticks(rotation=45)
            plt.grid(True, linestyle='--', alpha=0.7)

        else:  # bar default
            sorted_items = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)
            cats, vals = zip(*sorted_items)
            bars = plt.bar(cats, vals, color=colors[:len(cats)])
            plt.bar_label(bars, fmt='{:,.0f}', padding=3)
            plt.title(f"Pengeluaran per Kategori - {period.upper()}")
            plt.xlabel("Kategori")
            plt.ylabel("Nominal (Rp)")
            plt.xticks(rotation=45, ha='right')
            plt.grid(axis='y', linestyle='--', alpha=0.7)

        plt.tight_layout()
        filename = f"chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        plt.savefig(filename, dpi=300, bbox_inches='tight')
        plt.close()

        await update.message.reply_photo(
            photo=open(filename, "rb"),
            caption=f"Grafik {chart_type.upper()} {data_filter.capitalize()} periode {period}\n"
                    f"Total: Rp {sum(category_totals.values() if chart_type != 'line' else monthly_trend.values()):,}"
        )

        # Bersihkan file setelah kirim
        os.remove(filename)

    except Exception as e:
        print(f"ERROR chart: {str(e)}")
        print(traceback.format_exc())
        await update.message.reply_text(f"Error bikin chart: {str(e)}\nCoba periode lain atau cek data di sheet")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed_user(update, context):
        return
    keyboard = [
        [InlineKeyboardButton("Cek Saldo", callback_data='saldo')],
        [InlineKeyboardButton("Lihat Chart", callback_data='chart')],
        [InlineKeyboardButton("Catat Transaksi", callback_data='transaksi')],
        [InlineKeyboardButton("Hapus Transaksi", callback_data='hapus')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Menu Cepat Bot Keuangan Pro:\n"
        "Pilih salah satu tombol di bawah atau ketik perintah langsung\n\n"
        "Fitur lengkap:\n"
        "• Catat transaksi: BCA 50rb makan\n"
        "• Transfer: transfer BCA 100rb ke GOPAY\n"
        "• Cek saldo: /saldo\n"
        "• Chart: /chart 2025-02\n"
        "• Hapus: /hapus 10 atau /hapus terakhir\n"
        "• Laporan: /laporan\n"
        "• Reload User: /reloaduser (khusus owner)\n"
        "• Help: /help atau /menu",
        reply_markup=reply_markup
    )

async def laporan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed_user(update, context):
        return
    try:
        data = transaksi_sheet.get_all_values()[1:]
        if not data:
            await update.message.reply_text("Belum ada transaksi bro")
            return

        total_income = 0
        total_expense = 0
        top_expense_cat = {}
        for row in data:
            if len(row) < 7:
                continue
            tipe = row[3]
            amount = int(row[6]) if row[6].isdigit() else 0
            category = row[5]

            if tipe == "Income":
                total_income += amount
            else:
                total_expense += amount
                top_expense_cat[category] = top_expense_cat.get(category, 0) + amount

        net = total_income - total_expense
        top_cat = sorted(top_expense_cat.items(), key=lambda x: x[1], reverse=True)[:3]

        message = f"Laporan Keuangan Sekarang:\n\n"
        message += f"Total Pemasukan: Rp {total_income:,}\n"
        message += f"Total Pengeluaran: Rp {total_expense:,}\n"
        message += f"Net Saldo: Rp {net:,}\n\n"
        message += "Top 3 Pengeluaran:\n"
        for cat, amt in top_cat:
            message += f"• {cat}: Rp {amt:,}\n"

        await update.message.reply_text(message)
    except Exception as e:
        await update.message.reply_text(f"Error laporan: {str(e)}")

# Callback untuk inline keyboard
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'saldo':
        await saldo(update, context)
    elif query.data == 'chart_month':
        await chart(update, context)
    elif query.data == 'transfer':
        await query.edit_message_text("Kirim contoh transfer: transfer BCA 100rb ke GOPAY")
    elif query.data == 'hapus_last':
        await hapus(update, context)  # asumsikan ada fungsi hapus terakhir

async def reloaduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        await update.message.reply_text("Maaf, command ini hanya untuk owner bot.")
        return

    success = await load_allowed_users()
    if success:
        await update.message.reply_text(
            f"Reload user berhasil! Sekarang ada {len(ALLOWED_USER_IDS)} user aktif diizinkan.\n"
            f"User ID yang diizinkan: {', '.join(map(str, sorted(ALLOWED_USER_IDS)))}"
        )
    else:
        await update.message.reply_text("Gagal reload dari sheet USER. Cek logs atau sheetnya.")

# Callback untuk inline keyboard
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'saldo':
        await saldo(update, context)
    elif query.data == 'chart':
        await query.edit_message_text("Kirim perintah: /chart 2025-02")
    elif query.data == 'transaksi':
        await query.edit_message_text("Kirim contoh: BCA 50rb makan")
    elif query.data == 'hapus':
        await query.edit_message_text("Kirim: /hapus 10 atau /hapus terakhir")

# ================= MESSAGE HANDLER =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed_user(update, context):
        return
    try:
        text = update.message.text.strip()
        original_text = text
        text_lower = text.lower()

        user_name = update.message.from_user.first_name
        print(f"DEBUG: Pesan masuk dari {user_name}: '{original_text}'")

        # 0. Pesan terlalu pendek atau chit-chat → balas ramah
        if len(text_lower.split()) <= 1 or text_lower in ["halo", "hai", "tes", "test", "ok", "bro"]:
            await update.message.reply_text(
                f"Halo {user_name}! 👋\n"
                "Mau catat transaksi apa hari ini?\n\n"
                "Contoh cepat:\n"
                "• BCA 75rb makan warteg\n"
                "• gopay 2jt gaji\n"
                "• transfer mandiri 300rb ke dana bayar tagihan\n\n"
                "Atau ketik /saldo /chart /hapus /help"
            )
            return

        categories = load_categories()
        if not categories:
            await update.message.reply_text("Maaf bro, kategori ga bisa di-load. Cek sheet Categories ya.")
            return

        # 1. Deteksi transfer dulu (prioritas tinggi)
        transfer_keywords = ["transfer", "kirim", "ke", "tujuan", "ke ", "kirim ke", "transfer ke"]
        if any(kw in text_lower for kw in transfer_keywords):
            # Parsing nominal
            nominal = None
            nominal_idx = -1
            for i, p in enumerate(text_lower.split()):
                try:
                    nominal = parse_nominal(p)
                    nominal_idx = i
                    break
                except:
                    continue

            if nominal is None:
                await update.message.reply_text("Nominal transfernya mana bro? Contoh: transfer BCA 500rb ke GOPAY")
                return

            # Cari akun asal & tujuan dengan fuzzy + posisi
            possible_accounts = []
            for p in text_lower.split():
                if account_exists(p.upper()):
                    possible_accounts.append(p.upper())

            from_acc = None
            to_acc = None

            # Logika pintar: akun sebelum nominal biasanya asal, setelah "ke" biasanya tujuan
            if nominal_idx > 0:
                from_candidate = text_lower.split()[nominal_idx - 1].upper()
                if account_exists(from_candidate):
                    from_acc = from_candidate

            # Cari posisi "ke" atau keyword tujuan
            ke_pos = -1
            for kw in transfer_keywords:
                if kw in text_lower:
                    ke_pos = text_lower.find(kw)
                    break

            if ke_pos != -1:
                remaining = text_lower[ke_pos + len(kw):].strip().split()
                if remaining:
                    to_candidate = remaining[0].upper()
                    if account_exists(to_candidate):
                        to_acc = to_candidate

            # Fallback: ambil dua akun pertama yang ditemukan
            if not from_acc and not to_acc and len(possible_accounts) >= 2:
                from_acc = possible_accounts[0]
                to_acc = possible_accounts[1]
            elif not to_acc and len(possible_accounts) >= 1:
                to_acc = possible_accounts[-1]  # terakhir biasanya tujuan

            if not from_acc or not to_acc:
                await update.message.reply_text(
                    f"Akun asal/tujuan ga ketemu bro.\n"
                    f"Akun yang dikenal: {', '.join([a for a in possible_accounts]) or 'belum ada'}\n"
                    "Contoh benar: transfer BCA 500rb ke GOPAY"
                )
                return

            current_balance = get_current_balance(from_acc)
            if current_balance < nominal:
                await update.message.reply_text(
                    f"Saldo {from_acc} kurang bro 😔\n"
                    f"Sekarang: Rp {current_balance:,}\n"
                    f"Butuh: Rp {nominal:,}\n"
                    f"Kurang: Rp {nominal - current_balance:,}"
                )
                return

            tanggal = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            nama = user_name

            # Catat Expenses & Income
            transaksi_sheet.append_row([
                tanggal, nama, from_acc, "Expenses", "Financial", "Transfer", nominal, f"Transfer ke {to_acc} ({original_text})"
            ])
            transaksi_sheet.append_row([
                tanggal, nama, to_acc, "Income", "Financial", "Transfer", nominal, f"Transfer dari {from_acc} ({original_text})"
            ])

            new_balance_from = get_current_balance(from_acc)
            new_balance_to = get_current_balance(to_acc)

            await update.message.reply_text(
                f"Transfer berhasil bro! 🔥\n"
                f"Rp {nominal:,} dari {from_acc} → {to_acc}\n"
                f"Deskripsi: {original_text}\n\n"
                f"Saldo sekarang:\n"
                f"• {from_acc}: Rp {new_balance_from:,}\n"
                f"• {to_acc}: Rp {new_balance_to:,}"
            )
            return

        # 2. Transaksi biasa (pengeluaran/pemasukan)
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
            await update.message.reply_text("Nominalnya ga kebaca bro. Contoh: 500rb, 1jt, 75000")
            return

        # Cari akun dengan fuzzy (toleransi typo kecil)
        possible_accounts = []
        for p in parts:
            if account_exists(p.upper()):
                possible_accounts.append(p.upper())

        if not possible_accounts:
            await update.message.reply_text("Akun ga ketemu bro. Pastiin nama akun sama dengan di sheet Account.")
            return

        account = possible_accounts[0]  # ambil yang pertama, kalau banyak bisa tambah pilihan nanti

        # Kategori: fuzzy + gabung kata
        remaining = " ".join(parts[:nominal_idx] + parts[nominal_idx+1:])
        remaining_words = remaining.split()

        best_cat = None
        best_score = 0.0
        best_match_text = ""

        for cat in categories:
            sub_lower = cat["sub"].lower()
            # Per kata
            for word in remaining_words:
                score = difflib.SequenceMatcher(None, word, sub_lower).ratio()
                if score > best_score and score > 0.68:  # threshold pro
                    best_score = score
                    best_cat = cat
                    best_match_text = word

            # Gabung 2-3 kata
            for n in range(2, 4):
                if len(remaining_words) >= n:
                    combined = " ".join(remaining_words[-n:])
                    if combined in sub_lower or difflib.SequenceMatcher(None, combined, sub_lower).ratio() > 0.75:
                        best_cat = cat
                        best_match_text = combined
                        break

        if best_cat is None:
            await update.message.reply_text(
                f"Kategori '{remaining}' ga ketemu bro 😅\n"
                f"Coba pakai kata seperti: makan, transport, gaji, belanja, pulsa, tagihan\n"
                "Atau cek sheet Categories untuk daftar lengkap."
            )
            return

        # Deskripsi: sisa kata selain akun, nominal, kategori
        desc_parts = []
        for w in remaining_words:
            if w not in best_match_text.lower() and w not in account.lower():
                desc_parts.append(w)
        description = " ".join(desc_parts).strip() or original_text

        # Deteksi income kalau kategori Income
        if best_cat["type"] == "Income":
            tipe_display = "Pemasukan"
        else:
            current_balance = get_current_balance(account)
            if current_balance < nominal:
                await update.message.reply_text(
                    f"Saldo {account} kurang bro 😔\n"
                    f"Sekarang: Rp {current_balance:,}\n"
                    f"Butuh: Rp {nominal:,}\n"
                    f"Kurang: Rp {nominal - current_balance:,}\n"
                    "Top up dulu ya!"
                )
                return
            tipe_display = "Pengeluaran"

        # Catat ke sheet
        tanggal = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        nama = user_name

        transaksi_sheet.append_row([
            tanggal, nama, account, best_cat["type"],
            best_cat["parent"], best_cat["sub"], nominal, description
        ])

        new_balance = get_current_balance(account)

        await update.message.reply_text(
            f"Transaksi berhasil tercatat bro! ✅\n\n"
            f"• Akun: {account}\n"
            f"• Tipe: {tipe_display}\n"
            f"• Nominal: Rp {nominal:,}\n"
            f"• Kategori: {best_cat['sub']} ({best_cat['parent']})\n"
            f"• Deskripsi: {description}\n"
            f"• Waktu: {tanggal}\n\n"
            f"Saldo {account} sekarang: Rp {new_balance:,}"
        )

    except Exception as e:
        print(f"ERROR handle_message ({original_text}): {str(e)}")
        print(traceback.format_exc())
        await update.message.reply_text(
            "Waduh ada error nih bro 😅\n"
            f"{str(e)}\n\n"
            "Coba ketik ulang atau kirim format sederhana dulu ya.\n"
            "Kalau masih error, cek /start atau hubungi admin."
        )


        # Contoh fallback akhir kalau ga match
        await update.message.reply_text(
            "Inputnya agak aneh bro 😅\n"
            "Coba format seperti:\n"
            "BCA 50rb makan\n"
            "transfer BCA 100rb ke GOPAY\n\n"
            "Atau ketik /help untuk daftar lengkap"
        )

    except Exception as e:
        print(f"ERROR handle_message ({original_text}): {str(e)}")
        print(traceback.format_exc())
        await update.message.reply_text(
            "Waduh ada error nih bro 😅\n"
            f"{str(e)}\n\n"
            "Coba ketik ulang atau kirim format sederhana dulu ya.\n"
            "Kalau masih error, cek /start atau hubungi admin."
        )

# ================= APP =================
async def startup():
    await load_allowed_users()
    print("Startup selesai, allowed users loaded.")
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("saldo", saldo))
app.add_handler(CommandHandler("chart", chart))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(CommandHandler("menu", help_command))
app.add_handler(CommandHandler("reloaduser", reloaduser))
app.add_handler(CallbackQueryHandler(button_callback))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

try:
    # Jalankan startup async
    loop = asyncio.get_event_loop()
    loop.run_until_complete(startup())

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
    raise