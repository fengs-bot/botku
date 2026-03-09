import sys
import os
import json
import traceback
import difflib
from datetime import datetime, timedelta, time as dt_time  # rename class datetime.time jadi dt_time
import time  # modul time untuk time.time()
import asyncio
import csv
from collections import defaultdict

from zoneinfo import ZoneInfo  # Bawaan Python 3.9+, tidak perlu install

# ================= TIMEZONE GLOBAL =================
wib = ZoneInfo("Asia/Jakarta")

print("=== BOT MULAI JALAN DI RAILWAY ===")
print("Python version:", sys.version)
print("Current working dir:", os.getcwd())
print("TOKEN ada?", "TOKEN" in os.environ)
print("GOOGLE_CREDS ada?", "GOOGLE_CREDS" in os.environ)
print("WEBHOOK_URL ada?", "WEBHOOK_URL" in os.environ)
print("PORT from env:", os.environ.get("PORT", "tidak ada"))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

import gspread
from google.oauth2.service_account import Credentials

# ================= STATE KONFIRMASI HAPUS =================
hapus_pending = defaultdict(dict)   # user_id → {'row': int, 'timestamp': float, 'chat_id': int}

def format_rupiah(angka):
    try:
        num = int(str(angka).replace(",", "").replace(".", ""))
        return f"{num:,}"
    except:
        return str(angka)

# ================= ENV =================
TOKEN = os.getenv("TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.environ.get("PORT", 8080))

if not TOKEN:
    raise ValueError("TOKEN environment variable tidak ditemukan!")
if not WEBHOOK_URL:
    raise ValueError("WEBHOOK_URL environment variable tidak ditemukan!")

# ================= PRIVASI & USER MANAGEMENT =================
OWNER_ID = 6901833402
ALLOWED_USER_IDS = set()

def load_allowed_users_sync():
    global ALLOWED_USER_IDS
    try:
        user_sheet = spreadsheet.worksheet("USER")
        user_data = user_sheet.get_all_values()[1:]
        allowed = set()
        for row in user_data:
            if len(row) >= 1 and row[0].strip().isdigit():
                user_id = int(row[0].strip())
                status = row[2].strip().lower() if len(row) > 2 else "active"
                if status == "active":
                    allowed.add(user_id)
        ALLOWED_USER_IDS = allowed
        print(f"Loaded {len(allowed)} allowed users")
    except gspread.exceptions.WorksheetNotFound:
        print("WARNING: Sheet 'USER' tidak ditemukan → bot jadi public")
        ALLOWED_USER_IDS = set()
    except Exception as e:
        print(f"ERROR load allowed users: {e}")
        ALLOWED_USER_IDS = set()

async def is_allowed_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USER_IDS:
        await update.message.reply_text("Maaf bro, bot ini privat. Hanya user terdaftar yang bisa pakai.")
        return False
    return True

# ================= GOOGLE SHEETS =================
scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def load_keywords_mapping():
    try:
        kw_sheet = spreadsheet.worksheet("Keywords")
        data = kw_sheet.get_all_values()[1:]  # skip header
        mapping = defaultdict(list)           # sub.lower() → [keyword1, keyword2, ...]
        for row in data:
            if len(row) >= 2:
                sub = row[0].strip()
                keyword = row[1].strip().lower()
                if sub and keyword:
                    mapping[sub.lower()].append(keyword)
        print(f"Loaded {sum(len(v) for v in mapping.values())} keywords untuk {len(mapping)} sub-kategori")
        return mapping
    except gspread.exceptions.WorksheetNotFound:
        print("Sheet 'Keywords' TIDAK DITEMUKAN → matching pakai mode standar saja")
        return {}
    except Exception as e:
        print(f"Error load Keywords sheet: {e}")
        return {}

try:
    google_creds_json = os.getenv("GOOGLE_CREDS")
    if not google_creds_json:
        raise ValueError("GOOGLE_CREDS environment variable tidak ditemukan!")
    
    creds_dict = json.loads(google_creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
    
    client = gspread.authorize(creds)
    spreadsheet = client.open("BOT_KEUANGAN")
    keyword_mapping = load_keywords_mapping()

    category_sheet = spreadsheet.worksheet("Categories")
    account_sheet = spreadsheet.worksheet("Account")
except Exception as e:
    print("ERROR saat koneksi Google Sheets:")
    print(traceback.format_exc())
    raise

# ================= HELPER FUNCTIONS =================

def get_transaksi_sheet_by_year(year: str):
    sheet_name = f"Transaksi_{year}"
    try:
        return spreadsheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=sheet_name, rows="1000", cols="8")
        ws.append_row(["Tanggal","User","Account","Type","Parent","Sub","Nominal","Deskripsi"])
        return ws

def get_current_year_sheet():
    year = datetime.now(wib).strftime("%Y")
    return get_transaksi_sheet_by_year(year)

def parse_sheet_amount(value):
    try:
        clean_value = str(value).replace("Rp", "").replace(",", "").strip()
        return int(float(clean_value))
    except:
        return 0

def parse_nominal(nominal_text):
    try:
        nominal_text = str(nominal_text).lower().replace(".", "").replace(",", "").strip()
        if "-" in nominal_text:
            raise ValueError("Nominal tidak boleh minus.")
        if "jt" in nominal_text:
            value = int(float(nominal_text.replace("jt", "")) * 1_000_000)
        elif "rb" in nominal_text or "k" in nominal_text:
            value = int(float(nominal_text.replace("rb", "").replace("k", "")) * 1_000)
        else:
            value = int(nominal_text)
        if value <= 0:
            raise ValueError("Nominal harus lebih dari 0.")
        return value
    except Exception as e:
        raise ValueError(f"Nominal tidak valid: {nominal_text} → {str(e)}")

def account_exists(account_name):
    try:
        data = account_sheet.get_all_values()[1:]
        accounts = {row[0].strip().upper() for row in data if row and row[0].strip()}
        return account_name.strip().upper() in accounts
    except:
        return False

def get_current_balance(account_name):
    try:
        data = account_sheet.get_all_values()[1:]
        for row in data:
            if row and row[0].strip().upper() == account_name.strip().upper():
                if len(row) > 4:
                    clean = str(row[4]).replace(",", "").replace(".", "").strip()
                    return int(clean) if clean.isdigit() else 0
        return 0
    except Exception as e:
        print(f"Error get balance {account_name}: {e}")
        return 0

def update_account_balance(account_name: str, new_balance: int):
    """Update saldo akhir di sheet Account tanpa nimpa rumus lain."""
    try:
        acc_data = account_sheet.get_all_values()
        for idx, row in enumerate(acc_data[1:], start=2):  # mulai dari baris 2 (skip header)
            if row and row[0].strip().upper() == account_name.upper():
                account_sheet.update_cell(idx, 5, new_balance)  # kolom E (index 5) = Saldo Akhir
                print(f"Saldo {account_name} diupdate ke Rp {new_balance:,} di sheet Account")
                return
        print(f"Akun {account_name} tidak ditemukan di sheet Account untuk update saldo")
    except Exception as e:
        print(f"Gagal update saldo sheet Account untuk {account_name}: {e}")

def load_categories():
    try:
        data = category_sheet.get_all_values()[1:]
        return [{"type": r[0], "parent": r[1], "sub": r[2]} for r in data if len(r) >= 3]
    except:
        return []
    
def get_budget_sheet():
    try:
        return spreadsheet.worksheet("Budget")
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title="Budget", rows="1000", cols="3")
        ws.append_row(["Sub Kategori", "Budget Bulanan", "Catatan"])
        return ws

# ================= COMMANDS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed_user(update, context):
        return
    await update.message.reply_text("Bot aktif 24 jam 🚀 Selamat datang bro!")

async def hapus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("DEBUG: type of time:", type(time))  # harus <class 'module'>
    print("DEBUG: time.time() callable?", callable(time.time))  # harus True
    # ... kode selanjutnya
    if not await is_allowed_user(update, context):
        return
    
    if not context.args:
        await update.message.reply_text(
            "Format:\n"
            "/hapus <nomor baris>\n"
            "/hapus terakhir\n\n"
            "Contoh: /hapus 5  atau  /hapus terakhir"
        )
        return

    arg = context.args[0].lower()
    user_id = update.effective_user.id

    try:
        sheet = get_current_year_sheet()
        all_data = sheet.get_all_values()
        if len(all_data) <= 1:
            await update.message.reply_text("Belum ada transaksi yang bisa dihapus.")
            return

        if arg == "terakhir":
            row_to_delete = len(all_data)
        else:
            row_to_delete = int(arg)
            if row_to_delete < 2:
                await update.message.reply_text("Baris minimal 2 (header dihitung baris 1).")
                return
            if row_to_delete > len(all_data):
                await update.message.reply_text(f"Baris {row_to_delete} tidak ada.")
                return

        transaksi = all_data[row_to_delete - 1]
        tanggal = transaksi[0]
        akun = transaksi[2]
        tipe = transaksi[3]
        nominal = format_rupiah(transaksi[6])

        hapus_pending[user_id] = {
            'row': row_to_delete,
            'timestamp': time.time(),
            'chat_id': update.effective_chat.id
        }

        await update.message.reply_text(
            f"Yakin hapus transaksi ini?\n\n"
            f"Baris: {row_to_delete}\n"
            f"Tanggal: {tanggal}\n"
            f"Akun: {akun}\n"
            f"Tipe: {tipe}\n"
            f"Nominal: Rp {nominal}\n\n"
            "Balas **YA** atau **ya** dalam 30 detik untuk konfirmasi.\n"
            "Balas apa saja selain YA untuk batal."
        )

    except ValueError:
        await update.message.reply_text("Masukkan nomor baris yang valid (angka).")
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")

# State untuk pending edit (mirip hapus_pending)
edit_pending = defaultdict(dict)  # user_id → {'row': int, 'timestamp': float, 'chat_id': int, 'old_data': dict}

async def edit_transaksi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed_user(update, context):
        return

    if not context.args:
        await update.message.reply_text(
            "Format:\n"
            "/edit <nomor baris>\n\n"
            "Contoh:\n"
            "/edit 15"
        )
        return

    try:
        arg = context.args[0]
        row_to_edit = int(arg)
        if row_to_edit < 2:
            await update.message.reply_text("Baris minimal 2 (header dihitung baris 1).")
            return

        sheet = get_current_year_sheet()
        all_data = sheet.get_all_values()
        if row_to_edit > len(all_data):
            await update.message.reply_text(f"Baris {row_to_edit} tidak ada.")
            return

        transaksi = all_data[row_to_edit - 1]
        tanggal = transaksi[0]
        user = transaksi[1]
        akun = transaksi[2]
        tipe = transaksi[3]
        parent = transaksi[4]
        sub = transaksi[5]
        nominal = format_rupiah(transaksi[6])
        desk = transaksi[7] if len(transaksi) > 7 else "-"

        user_id = update.effective_user.id
        edit_pending[user_id] = {
            'row': row_to_edit,
            'timestamp': time.time(),
            'chat_id': update.effective_chat.id,
            'old_data': {
                'user': user,
                'akun': akun,
                'tipe': tipe,
                'parent': parent,
                'sub': sub,
                'nominal': transaksi[6],
                'desk': desk
            }
        }

        await update.message.reply_text(
            f"Edit transaksi baris {row_to_edit}:\n\n"
            f"Tanggal (tidak bisa diubah): {tanggal}\n"
            f"User: {user}\n"
            f"Akun: {akun}\n"
            f"Tipe: {tipe}\n"
            f"Parent: {parent}\n"
            f"Sub/Kategori: {sub}\n"
            f"Nominal: Rp {nominal}\n"
            f"Deskripsi: {desk}\n\n"
            "Balas dengan data baru yang ingin diubah (dalam 60 detik):\n"
            "Contoh:\n"
            "  BCA 45000 jajan baru           → ubah akun, nominal, deskripsi\n"
            "  ubah nominal 50000              → hanya ubah nominal\n"
            "  ubah akun GOPAY                 → hanya ubah akun\n"
            "  ubah kategori Makan             → hanya ubah sub kategori\n"
            "  ubah deskripsi makan malam      → hanya ubah deskripsi\n\n"
            "Balas apa saja selain format di atas untuk batal."
        )

    except ValueError:
        await update.message.reply_text("Masukkan nomor baris yang valid (angka).")
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")


async def handle_edit_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = edit_pending.get(user_id)
    if not state:
        return  # bukan balasan edit

    if time.time() - state['timestamp'] > 60:
        edit_pending.pop(user_id, None)
        await update.message.reply_text("Waktu edit kadaluarsa.")
        return

    text = update.message.text.strip()
    text_lower = text.lower()
    old = state['old_data']
    sheet = get_current_year_sheet()
    row = state['row']

    updated = False

    # Parsing sederhana balasan user
    if "ubah nominal" in text_lower:
        try:
            new_nominal_str = text.split("ubah nominal")[-1].strip()
            new_nominal = parse_nominal(new_nominal_str)
            sheet.update_cell(row, 7, new_nominal)  # kolom G (index 7)
            updated = True
        except:
            await update.message.reply_text("Format nominal salah.")
            return

    elif "ubah akun" in text_lower:
        new_akun = text.split("ubah akun")[-1].strip().upper()
        if account_exists(new_akun):
            sheet.update_cell(row, 3, new_akun)  # kolom C
            updated = True
        else:
            await update.message.reply_text(f"Akun '{new_akun}' tidak ditemukan.")
            return

    elif "ubah kategori" in text_lower or "ubah sub" in text_lower:
        new_sub = text.split("ubah kategori")[-1].strip() if "ubah kategori" in text_lower else text.split("ubah sub")[-1].strip()
        categories = load_categories()
        best_cat = None
        for cat in categories:
            if cat["sub"].lower() == new_sub.lower():
                best_cat = cat
                break
        if best_cat:
            sheet.update_cell(row, 4, best_cat["type"])   # Type
            sheet.update_cell(row, 5, best_cat["parent"]) # Parent
            sheet.update_cell(row, 6, best_cat["sub"])    # Sub
            updated = True
        else:
            await update.message.reply_text(f"Kategori '{new_sub}' tidak ditemukan.")
            return

    elif "ubah deskripsi" in text_lower:
        new_desk = text.split("ubah deskripsi")[-1].strip()
        sheet.update_cell(row, 8, new_desk)  # kolom H
        updated = True

    else:
        # Asumsi full edit (mirip input transaksi baru)
        parts = text_lower.split()
        new_account = None
        new_nominal = None
        new_desk = text

        for p in parts:
            if account_exists(p.upper()):
                new_account = p.upper()
            try:
                new_nominal = parse_nominal(p)
            except:
                pass

        if new_nominal:
            sheet.update_cell(row, 7, new_nominal)
            updated = True
        if new_account:
            sheet.update_cell(row, 3, new_account)
            updated = True
        if new_desk and new_desk != text:
            sheet.update_cell(row, 8, new_desk)
            updated = True

        # Rematch kategori kalau deskripsi berubah
        if updated:
            categories = load_categories()
            best_cat = None
            best_score = 0.0
            for cat in categories:
                sub_lower = cat["sub"].lower()
                if sub_lower in text_lower:
                    best_cat = cat
                    best_score = 1.0
                    break
                for word in parts:
                    score = difflib.SequenceMatcher(None, word, sub_lower).ratio()
                    if score > best_score and score >= 0.7:
                        best_score = score
                        best_cat = cat
            if best_cat:
                sheet.update_cell(row, 4, best_cat["type"])
                sheet.update_cell(row, 5, best_cat["parent"])
                sheet.update_cell(row, 6, best_cat["sub"])

    if updated:
        await update.message.reply_text(f"✅ Transaksi baris {row} berhasil diedit!")
    else:
        await update.message.reply_text("Tidak ada perubahan yang terdeteksi. Coba lagi dengan format yang benar.")

    edit_pending.pop(user_id, None)

async def saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed_user(update, context):
        return

    try:
        account_data = account_sheet.get_all_values()[1:]

        message = "💰 SALDO PER AKUN\n\n"
        total_all = 0

        import re

        for row in account_data:
            if not row or not row[0].strip():
                continue

            account_name = row[0].strip()

            # Kolom E = index 4 (Saldo Akhir)
            if len(row) > 4:
                clean_value = re.sub(r"[^\d]", "", str(row[4]))
                balance = int(clean_value) if clean_value else 0
            else:
                balance = 0

            total_all += balance
            message += f"{account_name} : Rp {balance:,}\n"

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
        if period == "all":
            await update.message.reply_text(
                "Chart all tahun tidak diizinkan. Gunakan /chart 2026 atau 2026-02"
            )
            return

        if "-" in period:
            year = period[:4]
        elif len(period) == 4:
            year = period
        else:
            year = datetime.now(wib).strftime("%Y")

        year_sheet = get_transaksi_sheet_by_year(year)
        data = year_sheet.get_all_values()[1:]
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
            category = row[5].strip() if len(row) > 5 else ""  # kolom Sub/Category
            try:
                amount = parse_sheet_amount(row[6])
            except:
                continue

            # SKIP TRANSFER IN/OUT (filter berdasarkan category/sub)
            if "transfer" in category.lower() or "transfer out" in category.lower() or "transfer in" in category.lower():
                continue

            # Filter data sesuai request
            if data_filter == 'expenses' and tipe != "Expenses":
                continue
            if data_filter == 'income' and tipe != "Income":
                continue

            # Proses periode (tetap sama)
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
        filename = f"chart_{datetime.now(wib).strftime('%Y%m%d_%H%M%S')}.png"
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

    message = "📱 **Menu Lengkap Bot Catat Duit Pro**\n\n"

    message += "🔹 **Cara Catat Transaksi Cepat** (tanpa command)\n"
    message += "Cukup ketik:  akun nominal deskripsi\n"
    message += "Contoh:\n"
    message += "• BCA 50rb makan\n"
    message += "• gopay 15rb transport grab\n"
    message += "• spbank 100rb gaji\n\n"

    message += "🔹 **Transfer Antar Akun**\n"
    message += "transfer <dari> <nominal> ke <ke>\n"
    message += "Contoh: transfer BCA 200rb ke GOPAY\n\n"

    message += "🔹 **Perintah Utama**\n"
    message += "• /start          → Aktifkan & sambutan\n"
    message += "• /saldo          → Cek saldo semua akun + total\n"
    message += "• /riwayat <akun> → 10 transaksi terakhir akun tertentu\n"
    message += "  Contoh: /riwayat BCA  atau  /history GOPAY\n"
    message += "• /recent atau /riwayatterakhir → 10 transaksi terakhir dari semua akun\n"
    message += "• /kategori <sub> → Riwayat transaksi di kategori tertentu\n"
    message += "• /ringkasan      → Ringkasan hari ini, minggu ini, bulan ini\n"
    message += "• /laporan        → Total income, expense, net (tahun ini)\n"
    message += "  Tambah tahun: /laporan 2026  atau /laporan all\n"
    message += "• /chart [periode] [tipe] [filter]\n"
    message += "  Contoh:\n"
    message += "  • /chart 2026-03           → Bar pengeluaran Maret 2026\n"
    message += "  • /chart 2026-03 pie       → Pie chart Maret\n"
    message += "  • /chart 2026 line         → Trend bulanan 2026\n"
    message += "  • /chart 2026 expenses     → Pengeluaran 2026\n"
    message += "• /export         → Download semua transaksi tahun ini (.csv)\n"
    message += "• /hapus <nomor>  → Hapus transaksi (konfirmasi YA)\n"
    message += "  • /hapus terakhir → Hapus transaksi paling baru\n\n"

    message += "🔹 **Manajemen Kategori** (semua user bisa lihat)\n"
    message += "• /kategori atau /daftarkategori\n"
    message += "  → Lihat semua kategori yang dikenali bot (grouped)\n\n"

    message += "🔹 **Command Owner Only** (hanya Fengky)\n"
    message += "• /reloaduser     → Refresh daftar user dari sheet USER\n"
    message += "• /tambahkategori <Type> <Parent> <Sub>\n"
    message += "  Contoh: /tambahkategori Expenses Fixed_Expenses Biaya_Admin\n"
    message += "• /editkategori <Old Sub> <New Type> <New Parent> <New Sub>\n"
    message += "  Contoh: /editkategori Cuci Mobil Expenses Lifestyle Cuci Kendaraan\n"
    message += "• /hapuskategori <Sub Kategori>\n"
    message += "  Contoh: /hapuskategori Cuci Mobil\n\n"

    message += "Tips:\n"
    message += "• Nominal bisa: 50rb, 1jt, 750k, 1000000\n"
    message += "• Kalau kategori tidak dikenali → bot akan tolak & kasih saran\n"
    message += "• Tambah/edit/hapus kategori langsung dari Telegram (owner)\n"
    message += "• Semua transaksi otomatis masuk sheet tahun berjalan\n\n"

    message += "• /addrecurring   → Tambah recurring baru\n"
    message += "• /listrecurring  → Lihat semua recurring\n"
    message += "• /togglerecurring <ID> on/off → Aktif/Nonaktif\n"
    message += "• /deleterecurring <ID> → Hapus recurring\n"

    message += "• /setbudget <kategori> <nominal> → Set budget bulanan per kategori (berlaku selamanya)\n"
    message += "• /editbudget <kategori> <nominal baru> → Ubah budget yang sudah ada\n"
    message += "• /hapusbudget <kategori> → Hapus budget kategori tertentu\n"
    message += "• /budget → Cek status budget bulan ini (terpakai vs sisa)\n"

    message += "Kalau ada kendala atau ide fitur baru, langsung bilang aja bro! 🔥"

    await update.message.reply_text(message, parse_mode="Markdown")

async def laporan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed_user(update, context):
        return

    try:
        summary_sheet = spreadsheet.worksheet("Summary")
        data = summary_sheet.get_all_values()[1:]

        if not data:
            await update.message.reply_text("Sheet Summary kosong bro.")
            return

        args = context.args
        total_income = 0
        total_expense = 0

        if args:
            year_input = args[0]

            if year_input.lower() == "all":
                for row in data:
                    total_income += int(row[1])
                    total_expense += int(row[2])
            else:
                for row in data:
                    if row[0] == year_input:
                        total_income = int(row[1])
                        total_expense = int(row[2])
                        break
        else:
            current_year = datetime.now(wib).strftime("%Y")
            for row in data:
                if row[0] == current_year:
                    total_income = int(row[1])
                    total_expense = int(row[2])
                    break

        net = total_income - total_expense

        message = (
            f"📊 LAPORAN KEUANGAN\n\n"
            f"Income : Rp {total_income:,}\n"
            f"Expense : Rp {total_expense:,}\n"
            f"Net : Rp {net:,}"
        )

        await update.message.reply_text(message)

    except Exception as e:
        await update.message.reply_text(f"Error laporan: {str(e)}")

async def ringkasan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed_user(update, context):
        return

    try:
        year = datetime.now(wib).strftime("%Y")
        year_sheet = get_transaksi_sheet_by_year(year)
        data = year_sheet.get_all_values()[1:]
        if not data:
            await update.message.reply_text("Belum ada transaksi bro")
            return

        today = datetime.now(wib).strftime("%Y-%m-%d")
        this_week_start = (datetime.now(wib) - timedelta(days=datetime.now(wib).weekday())).strftime("%Y-%m-%d")
        this_month = today[:7]

        daily_income = daily_expense = 0
        weekly_income = weekly_expense = 0
        monthly_income = monthly_expense = 0

        for row in data:
            if len(row) < 7:
                continue
            date_str = row[0][:10]  # YYYY-MM-DD
            tipe = row[3]
            amount = parse_sheet_amount(row[6])

            if tipe == "Income":
                if date_str == today:
                    daily_income += amount
                if date_str >= this_week_start:
                    weekly_income += amount
                if date_str.startswith(this_month):
                    monthly_income += amount
            else:
                if date_str == today:
                    daily_expense += amount
                if date_str >= this_week_start:
                    weekly_expense += amount
                if date_str.startswith(this_month):
                    monthly_expense += amount

        message = f"Ringkasan Keuangan:\n\n"
        message += f"**Hari ini ({today}):**\n"
        message += f"Pemasukan: Rp {daily_income:,}\n"
        message += f"Pengeluaran: Rp {daily_expense:,}\n"
        message += f"Net: Rp {daily_income - daily_expense:,}\n\n"

        message += f"**Minggu ini (sejak {this_week_start}):**\n"
        message += f"Pemasukan: Rp {weekly_income:,}\n"
        message += f"Pengeluaran: Rp {weekly_expense:,}\n"
        message += f"Net: Rp {weekly_income - weekly_expense:,}\n\n"

        message += f"**Bulan ini ({this_month}):**\n"
        message += f"Pemasukan: Rp {monthly_income:,}\n"
        message += f"Pengeluaran: Rp {monthly_expense:,}\n"
        message += f"Net: Rp {monthly_income - monthly_expense:,}"

        await update.message.reply_text(message)
    except Exception as e:
        await update.message.reply_text(f"Error ringkasan: {str(e)}")

async def riwayat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed_user(update, context):
        return

    if len(context.args) < 1:
        await update.message.reply_text("Format: /riwayat <akun>\nContoh: /riwayat BCA")
        return

    akun = context.args[0].upper()
    if not account_exists(akun):
        await update.message.reply_text(f"Akun '{akun}' ga ketemu di sheet Account.")
        return

    try:
        # ─── INI YANG DIPERBAIKI ───
        # Ganti transaksi_sheet jadi get_current_year_sheet()
        sheet = get_current_year_sheet()   # atau get_transaksi_sheet_by_year("2026") kalau spesifik tahun
        data = sheet.get_all_values()[1:]  # skip header
        
        # Filter transaksi berdasarkan akun
        transaksi_akun = [
            row for row in data 
            if len(row) >= 7 and row[2].strip().upper() == akun
        ]
        
        # Urut terbaru (berdasarkan tanggal kolom 0)
        transaksi_akun.sort(key=lambda x: x[0], reverse=True)

        if not transaksi_akun:
            await update.message.reply_text(f"Belum ada transaksi di akun {akun}.")
            return

        message = f"Riwayat 10 transaksi terakhir di {akun}:\n\n"
        for row in transaksi_akun[:10]:
            tanggal = row[0]
            tipe = row[3]
            kategori = row[5]
            nominal = parse_sheet_amount(row[6])
            desk = row[7] if len(row) > 7 else "-"
            sign = "+" if tipe == "Income" else "-"
            message += f"{tanggal} | {sign}Rp {nominal:,} | {kategori} | {desk}\n"

        await update.message.reply_text(message)

    except Exception as e:
        await update.message.reply_text(f"Error riwayat: {str(e)}\nCoba lagi atau cek log.")

async def recent_transactions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed_user(update, context):
        return

    try:
        sheet = get_current_year_sheet()
        data = sheet.get_all_values()[1:]  # skip header
        
        if not data:
            await update.message.reply_text("Belum ada transaksi sama sekali bro.")
            return

        # Urutkan berdasarkan tanggal (kolom 0), terbaru dulu
        data.sort(key=lambda x: x[0], reverse=True)

        message = "🕒 **10 Transaksi Terakhir (Semua Akun)**\n\n"
        for row in data[:10]:
            if len(row) < 8:
                continue
            tanggal = row[0]
            user = row[1]
            akun = row[2]
            tipe = row[3]
            sub = row[5]
            nominal = parse_sheet_amount(row[6])
            desk = row[7] if len(row) > 7 else "-"
            sign = "+" if tipe == "Income" else "-"
            message += f"{tanggal} | {akun} | {sign}Rp {nominal:,} | {sub} | {desk}\n"

        await update.message.reply_text(message)

    except Exception as e:
        await update.message.reply_text(f"Error riwayat terakhir: {str(e)}\nCoba lagi atau cek log.")

async def kategori_riwayat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed_user(update, context):
        return

    if not context.args:
        await update.message.reply_text("Format: /kategori <sub kategori>\nContoh: /kategori Makan")
        return

    sub_search = " ".join(context.args).lower()

    try:
        sheet = get_current_year_sheet()
        data = sheet.get_all_values()[1:]
        
        transaksi_cat = [
            row for row in data 
            if len(row) >= 8 and row[5].strip().lower() == sub_search
        ]
        
        transaksi_cat.sort(key=lambda x: x[0], reverse=True)

        if not transaksi_cat:
            await update.message.reply_text(f"Belum ada transaksi di kategori '{sub_search.title()}'.")
            return

        message = f"Riwayat 10 transaksi terakhir '{sub_search.title()}':\n\n"
        for row in transaksi_cat[:10]:
            tanggal = row[0]
            akun = row[2]
            tipe = row[3]
            nominal = parse_sheet_amount(row[6])
            desk = row[7]
            sign = "+" if tipe == "Income" else "-"
            message += f"{tanggal} | {akun} | {sign}Rp {nominal:,} | {desk}\n"

        await update.message.reply_text(message)

    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")

async def export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed_user(update, context):
        return

    args = context.args
    filter_akun = args[0].upper() if args else None
    filter_bulan = args[0] if args and "-" in args[0] else None

    try:
        if filter_bulan:
            year_sheet = get_transaksi_sheet_by_year(filter_bulan[:4])
            data = year_sheet.get_all_values()
            # Filter bulan
            data = [row for row in data if len(row) >= 1 and row[0].startswith(filter_bulan)]
        else:
            sheet = get_current_year_sheet()
            data = sheet.get_all_values()

        if filter_akun:
            data = [row for row in data if len(row) >= 3 and row[2].strip().upper() == filter_akun]

        if len(data) <= 1:
            await update.message.reply_text("Tidak ada data yang match filter.")
            return

        filename = f"export_{filter_akun or filter_bulan or 'all'}_{datetime.now(wib).strftime('%Y%m%d_%H%M%S')}.csv"
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerows(data)

        await update.message.reply_document(
            document=open(filename, 'rb'),
            caption=f"Export {'akun ' + filter_akun if filter_akun else 'bulan ' + filter_bulan if filter_bulan else 'semua'} berhasil!"
        )

        os.remove(filename)

    except Exception as e:
        await update.message.reply_text(f"Error export: {str(e)}")

async def reloaduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    print(f"DEBUG: /reloaduser dipanggil oleh user ID {user_id}")

    if user_id != OWNER_ID:
        await update.message.reply_text("Maaf, command ini hanya untuk owner bot.")
        print("DEBUG: Bukan owner")
        return

    # Cek apakah owner sendiri diizinkan
    if user_id not in ALLOWED_USER_IDS:
        await update.message.reply_text("ID lu ga ada di daftar allowed (sheet USER). Cek sheet dulu!")
        print(f"DEBUG: Owner ditolak, ID {user_id} tidak di ALLOWED_USER_IDS {ALLOWED_USER_IDS}")
        return

    print("DEBUG: Mulai reload dari sheet USER")
    load_allowed_users_sync()
    print(f"DEBUG: Reload selesai, sekarang {len(ALLOWED_USER_IDS)} user")

    # BALASAN KE CHAT (INI YANG KURANG TADI)
    await update.message.reply_text(
        f"Reload user berhasil bro! 🔥\n"
        f"Sekarang ada {len(ALLOWED_USER_IDS)} user aktif diizinkan.\n"
        f"User ID yang terdaftar: {', '.join(map(str, sorted(ALLOWED_USER_IDS)))}\n\n"
        "Kalau ga berubah, cek sheet 'USER' dan pastiin ID lu ada di kolom A + 'active' di kolom C."
    )

async def daftar_kategori(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed_user(update, context):
        return

    try:
        categories = load_categories()
        if not categories:
            await update.message.reply_text("Sheet Categories kosong atau error.")
            return

        from collections import defaultdict
        
        # Grouping: Type → Parent → List of Subs
        grouped = defaultdict(lambda: defaultdict(list))
        for cat in categories:
            grouped[cat["type"]][cat["parent"]].append(cat["sub"])

        message = "📋 Daftar Kategori yang Dikenali Bot:\n\n"
        message += "Format: **Type** > **Parent** > Sub Kategori\n"
        message += "─" * 40 + "\n"

        for tipe in sorted(grouped):
            message += f"\n**{tipe.upper()}**\n"
            for parent in sorted(grouped[tipe]):
                message += f"  • **{parent}**\n"
                for sub in sorted(grouped[tipe][parent]):
                    message += f"    - {sub}\n"

        message += "\nGunakan salah satu sub-kategori di atas saat mencatat transaksi.\n"
        message += "Contoh: BCA makan 25rb → match ke Expenses > Daily Expenses > Makan\n"
        message += "Kalau gak ada yang cocok, tambah pake /tambahkategori (owner only)."

        await update.message.reply_text(message)

    except Exception as e:
        await update.message.reply_text(f"Error menampilkan daftar: {str(e)}")

async def tambah_kategori(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != OWNER_ID:
        await update.message.reply_text("Command ini hanya untuk owner bot.")
        return

    if len(context.args) != 3:
        await update.message.reply_text(
            "Format:\n"
            "/tambahkategori <Type> <Parent> <Sub Kategori>\n\n"
            "Contoh:\n"
            "/tambahkategori Expenses Daily Expenses Cuci Mobil\n"
            "/tambahkategori Income Other Hadiah Ulang Tahun\n\n"
            "Pastikan tanpa tanda kutip, pisah spasi."
        )
        return

    try:
        tipe = context.args[0]
        parent = context.args[1].replace("_", " ")  # ganti _ jadi spasi
        sub = context.args[2].replace("_", " ")     # ganti _ jadi spasi
        
        # Validasi sederhana
        if tipe not in ["Income", "Expenses"]:
            await update.message.reply_text("Type harus 'Income' atau 'Expenses' (huruf besar awal).")
            return

        category_sheet = spreadsheet.worksheet("Categories")
        
        # Cek apakah sudah ada (hindari duplikat)
        existing = category_sheet.get_all_values()[1:]
        for row in existing:
            if len(row) >= 3 and row[0] == tipe and row[1] == parent and row[2] == sub:
                await update.message.reply_text(f"Kategori '{sub}' sudah ada di {tipe} > {parent}.")
                return

        # Tambah baris baru
        category_sheet.append_row([tipe, parent, sub])
        
        # Reload categories di memory (supaya langsung terdeteksi)
        # (load_categories() akan dipanggil ulang saat handle_message berikutnya)
        
        await update.message.reply_text(
            f"✅ Kategori baru berhasil ditambahkan!\n\n"
            f"Type  : {tipe}\n"
            f"Parent: {parent}\n"
            f"Sub   : {sub}\n\n"
            "Sekarang bot sudah bisa mengenali kata kunci ini.\n"
            "Coba tes: ketik transaksi dengan kata '{sub.lower()}'"
        )

    except gspread.exceptions.WorksheetNotFound:
        await update.message.reply_text("Sheet 'Categories' tidak ditemukan.")
    except Exception as e:
        await update.message.reply_text(f"Error menambah kategori: {str(e)}")

async def edit_kategori(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != OWNER_ID:
        await update.message.reply_text("Command ini hanya untuk owner bot.")
        return

    if len(context.args) < 4:
        await update.message.reply_text(
            "Format:\n"
            "/editkategori <Old Sub Kategori> <New Type> <New Parent> <New Sub Kategori>\n\n"
            "Contoh:\n"
            "/editkategori Cuci Mobil Expenses Lifestyle Cuci Kendaraan\n\n"
            "Ini akan cari Sub lama (case-insensitive), lalu ubah ke yang baru.\n"
            "Pastikan tanpa tanda kutip, pisah spasi. Kalau Sub punya spasi, gabung dengan underscore dulu (nanti diganti spasi)."
        )
        return

    try:
        old_sub = " ".join(context.args[0:len(context.args)-3]).replace("_", " ")  # Support spasi di old_sub
        new_tipe = context.args[-3]
        new_parent = context.args[-2]
        new_sub = " ".join(context.args[-1:]).replace("_", " ")  # Support spasi di new_sub
        
        if new_tipe not in ["Income", "Expenses"]:
            await update.message.reply_text("New Type harus 'Income' atau 'Expenses'.")
            return

        category_sheet = spreadsheet.worksheet("Categories")
        data = category_sheet.get_all_values()
        
        found_row = None
        for idx, row in enumerate(data[1:], start=2):  # Mulai row 2 (data)
            if len(row) >= 3 and row[2].lower() == old_sub.lower():
                found_row = idx
                break

        if not found_row:
            await update.message.reply_text(f"Kategori dengan Sub '{old_sub}' tidak ditemukan.")
            return

        # Update row
        category_sheet.update_cell(found_row, 1, new_tipe)    # Kolom A: Type
        category_sheet.update_cell(found_row, 2, new_parent)  # Kolom B: Parent
        category_sheet.update_cell(found_row, 3, new_sub)     # Kolom C: Sub

        await update.message.reply_text(
            f"✅ Kategori berhasil diedit!\n\n"
            f"Lama: Sub '{old_sub}'\n"
            f"Baru: {new_tipe} > {new_parent} > {new_sub}"
        )

    except gspread.exceptions.WorksheetNotFound:
        await update.message.reply_text("Sheet 'Categories' tidak ditemukan.")
    except Exception as e:
        await update.message.reply_text(f"Error edit kategori: {str(e)}")

async def hapus_kategori(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != OWNER_ID:
        await update.message.reply_text("Command ini hanya untuk owner bot.")
        return

    if not context.args:
        await update.message.reply_text(
            "Format:\n"
            "/hapuskategori <Sub Kategori>\n\n"
            "Contoh: /hapuskategori Cuci Mobil\n"
            "Ini akan hapus row yang Sub-nya match (case-insensitive).\n"
            "Kalau Sub punya spasi, gabung dengan underscore (nanti diganti spasi)."
        )
        return

    try:
        sub_to_delete = " ".join(context.args).replace("_", " ")
        
        category_sheet = spreadsheet.worksheet("Categories")
        data = category_sheet.get_all_values()
        
        found_row = None
        for idx, row in enumerate(data[1:], start=2):
            if len(row) >= 3 and row[2].lower() == sub_to_delete.lower():
                found_row = idx
                break

        if not found_row:
            await update.message.reply_text(f"Kategori dengan Sub '{sub_to_delete}' tidak ditemukan.")
            return

        # Hapus row
        category_sheet.delete_rows(found_row)

        await update.message.reply_text(f"✅ Kategori '{sub_to_delete}' berhasil dihapus dari sheet!")

    except gspread.exceptions.WorksheetNotFound:
        await update.message.reply_text("Sheet 'Categories' tidak ditemukan.")
    except Exception as e:
        await update.message.reply_text(f"Error hapus kategori: {str(e)}")

async def add_recurring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("Command ini hanya untuk owner.")
        return

    if len(context.args) != 7:
        await update.message.reply_text(
            "Format:\n"
            "/addrecurring <akun> <nominal> <tipe> <parent> <sub> <frekuensi> <jadwal>\n\n"
            "Contoh:\n"
            "/addrecurring BCA 150000 Expenses Fixed_Expenses Internet monthly 10\n"
            "/addrecurring SPBANK 5000000 Income Salary_&_Work Gaji monthly 25\n\n"
            "Gunakan underscore _ untuk spasi di Parent atau Sub (bot akan ganti jadi spasi).\n"
            "Frekuensi: daily / weekly / monthly\n"
            "Jadwal: angka tanggal (monthly), hari (weekly), atau 'last_day' (monthly)"
        )
        return

    try:
        akun = context.args[0].upper()
        nominal_str = context.args[1]
        tipe = context.args[2].capitalize()
        parent = context.args[3].replace("_", " ")   # fix spasi
        sub = context.args[4].replace("_", " ")      # fix spasi
        frekuensi = context.args[5].lower()
        jadwal = context.args[6].lower()

        if not account_exists(akun):
            await update.message.reply_text(f"Akun '{akun}' tidak ditemukan.")
            return

        if tipe not in ["Income", "Expenses"]:
            await update.message.reply_text("Tipe harus 'Income' atau 'Expenses'.")
            return

        nominal = parse_nominal(nominal_str)

        recurring_sheet = spreadsheet.worksheet("Recurring")
        existing = recurring_sheet.get_all_values()
        new_id = len(existing)  # mulai dari 1 kalau header saja

        recurring_sheet.append_row([
            new_id, akun, nominal, tipe, parent, sub, "Auto recurring", frekuensi, jadwal, "Yes"
        ])

        await update.message.reply_text(
            f"✅ Recurring berhasil ditambahkan!\n\n"
            f"ID: {new_id}\n"
            f"Akun: {akun}\n"
            f"Nominal: Rp {nominal:,}\n"
            f"Kategori: {tipe} > {parent} > {sub}\n"
            f"Frekuensi: {frekuensi} pada {jadwal}\n"
            "Aktif: Yes"
        )

    except Exception as e:
        await update.message.reply_text(f"Error tambah recurring: {str(e)}")

async def list_recurring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("Command ini hanya untuk owner.")
        return

    try:
        recurring_sheet = spreadsheet.worksheet("Recurring")
        data = recurring_sheet.get_all_values()[1:]  # skip header
        if not data:
            await update.message.reply_text("Belum ada recurring yang terdaftar.")
            return

        message = "📅 **Daftar Recurring Aktif**\n\n"
        found_active = False

        for row in data:
            if len(row) < 10:
                continue  # row tidak lengkap

            if row[9].strip().lower() != "yes":
                continue  # hanya aktif

            found_active = True

            try:
                # Parse Nominal aman (kolom B, index 1)
                nominal_raw = row[1].strip()
                # Hapus Rp, koma, titik ribuan, dll
                nominal_clean = nominal_raw.replace("Rp", "").replace(".", "").replace(",", "").replace(" ", "")
                nominal = int(nominal_clean) if nominal_clean.isdigit() else 0
            except:
                nominal = 0  # kalau gagal parse, tampil 0

            # Tampil dengan format rupiah aman
            message += f"ID {row[0]}: {row[1]} | Rp {nominal:,} | {row[3]} > {row[5]} | {row[7]} {row[8]}\n"

        if not found_active:
            message += "Tidak ada recurring aktif saat ini."

        await update.message.reply_text(message)

    except Exception as e:
        await update.message.reply_text(f"Error list recurring: {str(e)}\nCek sheet Recurring: pastikan kolom Nominal (B) adalah angka murni tanpa 'Rp' atau format khusus. Coba ubah format cell ke 'Plain text' atau 'Number' tanpa thousand separator.")

async def toggle_recurring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("Command ini hanya untuk owner.")
        return

    if len(context.args) != 2:
        await update.message.reply_text("Format: /togglerecurring <ID> on/off")
        return

    try:
        rec_id = context.args[0]
        status = context.args[1].lower()
        if status not in ["on", "off"]:
            await update.message.reply_text("Status harus 'on' atau 'off'.")
            return

        recurring_sheet = spreadsheet.worksheet("Recurring")
        data = recurring_sheet.get_all_values()

        found = False
        for idx, row in enumerate(data[1:], start=2):
            if row and row[0] == rec_id:
                recurring_sheet.update_cell(idx, 10, "Yes" if status == "on" else "No")
                found = True
                break

        if found:
            await update.message.reply_text(f"Recurring ID {rec_id} diubah menjadi {'aktif' if status == 'on' else 'non-aktif'}.")
        else:
            await update.message.reply_text(f"ID {rec_id} tidak ditemukan.")

    except Exception as e:
        await update.message.reply_text(f"Error toggle recurring: {str(e)}")

async def delete_recurring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("Command ini hanya untuk owner.")
        return

    if not context.args:
        await update.message.reply_text("Format: /deleterecurring <ID>")
        return

    try:
        rec_id = context.args[0]

        recurring_sheet = spreadsheet.worksheet("Recurring")
        data = recurring_sheet.get_all_values()

        found_row = None
        for idx, row in enumerate(data[1:], start=2):
            if row and row[0] == rec_id:
                found_row = idx
                break

        if not found_row:
            await update.message.reply_text(f"ID {rec_id} tidak ditemukan.")
            return

        recurring_sheet.delete_rows(found_row)
        await update.message.reply_text(f"Recurring ID {rec_id} berhasil dihapus.")

    except Exception as e:
        await update.message.reply_text(f"Error delete recurring: {str(e)}")

async def budget_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed_user(update, context):
        return

    try:
        bulan = datetime.now(wib).strftime("%Y-%m")
        budget_sheet = get_budget_sheet()
        budget_data = budget_sheet.get_all_values()[1:]

        if not budget_data:
            await update.message.reply_text("Belum ada budget yang diset. Gunakan /setbudget dulu.")
            return

        trans_sheet = get_transaksi_sheet_by_year(bulan[:4])
        trans_data = trans_sheet.get_all_values()[1:]

        message = f"📊 **Status Budget Bulan {bulan}**\n\n"

        total_budget = 0
        total_used = 0

        for budget_row in budget_data:
            if len(budget_row) < 2:
                continue
            sub_cat = budget_row[0].strip()
            budget_amt = int(budget_row[1]) if budget_row[1].isdigit() else 0
            total_budget += budget_amt

            # Hitung pengeluaran real di kategori ini (bulan ini saja)
            used = 0
            for trans in trans_data:
                if len(trans) < 7:
                    continue
                date_str = trans[0][:7]
                tipe = trans[3]
                cat = trans[5].strip()
                amt = parse_sheet_amount(trans[6])
                if date_str == bulan and tipe == "Expenses" and cat.lower() == sub_cat.lower():
                    used += amt

            total_used += used
            sisa = budget_amt - used
            persen = (used / budget_amt * 100) if budget_amt > 0 else 0

            status_emoji = "🟢" if persen < 70 else "🟡" if persen < 90 else "🟠" if persen < 100 else "🔴"
            message += f"{status_emoji} **{sub_cat}**\n"
            message += f"  Budget: Rp {budget_amt:,}\n"
            message += f"  Terpakai: Rp {used:,} ({persen:.1f}%)\n"
            message += f"  Sisa: Rp {sisa:,}\n\n"

        if total_budget > 0:
            message += f"**Total Budget Bulanan: Rp {total_budget:,}**\n"
            message += f"**Total Terpakai Bulan Ini: Rp {total_used:,}**\n"
            message += f"**Sisa Keseluruhan: Rp {total_budget - total_used:,}**"

        await update.message.reply_text(message)

    except Exception as e:
        await update.message.reply_text(f"Error budget status: {str(e)}")

async def set_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed_user(update, context):
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "Format:\n"
            "/setbudget <sub-kategori> <nominal>\n\n"
            "Contoh:\n"
            "/setbudget Makan 1000000\n"
            "/setbudget Makan_&_Minum 650000\n"
            "Bisa pakai spasi atau underscore, bot akan samakan."
        )
        return

    try:
        sub_cat_input = context.args[0]
        nominal_str = context.args[1]

        nominal = parse_nominal(nominal_str)

        # Normalisasi input & kategori untuk match lebih mudah
        def normalize(text):
            return text.lower().replace("_", " ").replace("&", "dan").replace("  ", " ").strip()

        norm_input = normalize(sub_cat_input)

        categories = load_categories()
        best_match = None
        for cat in categories:
            norm_sub = normalize(cat["sub"])
            if norm_sub == norm_input:
                best_match = cat["sub"]  # pakai nama asli dari sheet
                break

        if not best_match:
            await update.message.reply_text(
                f"Sub-kategori '{sub_cat_input}' tidak ditemukan atau tidak cukup mirip.\n"
                "Cek dulu dengan /kategori (pastikan huruf, spasi, & sama persis).\n"
                "Contoh: kalau di sheet 'Makan & Minum', ketik persis seperti itu."
            )
            return

        budget_sheet = get_budget_sheet()
        existing = budget_sheet.get_all_values()[1:]

        row_to_update = None
        for idx, row in enumerate(existing, start=2):
            if len(row) >= 2 and normalize(row[0]) == norm_input:
                row_to_update = idx
                break

        if row_to_update:
            budget_sheet.update_cell(row_to_update, 2, nominal)
            msg = f"✅ Budget bulanan untuk '{best_match}' diperbarui menjadi Rp {nominal:,}"
        else:
            budget_sheet.append_row([best_match, nominal, ""])
            msg = f"✅ Budget bulanan baru ditambahkan: '{best_match}' Rp {nominal:,} (berlaku setiap bulan)"

        await update.message.reply_text(msg)

    except Exception as e:
        await update.message.reply_text(f"Error set budget: {str(e)}\nCoba lagi atau cek sheet Categories.")

async def edit_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed_user(update, context):
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "Format:\n"
            "/editbudget <sub-kategori> <nominal baru>\n\n"
            "Contoh:\n"
            "/editbudget Makan 1200000\n\n"
            "Sub-kategori harus sudah ada di budget sebelumnya."
        )
        return

    try:
        sub_cat_input = context.args[0]
        nominal_str = context.args[1]

        nominal = parse_nominal(nominal_str)

        # Cek apakah sub-kategori valid di Categories
        categories = load_categories()
        valid_sub = any(cat["sub"].strip().lower() == sub_cat_input.strip().lower() for cat in categories)
        if not valid_sub:
            await update.message.reply_text(
                f"Sub-kategori '{sub_cat_input}' tidak ditemukan di daftar kategori.\n"
                "Cek dulu dengan /kategori atau /daftarkategori."
            )
            return

        budget_sheet = get_budget_sheet()
        existing = budget_sheet.get_all_values()[1:]

        row_to_update = None
        for idx, row in enumerate(existing, start=2):
            if len(row) >= 2 and row[0].strip().lower() == sub_cat_input.strip().lower():
                row_to_update = idx
                break

        if not row_to_update:
            await update.message.reply_text(
                f"Budget untuk '{sub_cat_input}' belum pernah diset.\n"
                "Gunakan /setbudget dulu kalau mau tambah baru."
            )
            return

        # Update nominal di kolom B (index 2)
        budget_sheet.update_cell(row_to_update, 2, nominal)
        await update.message.reply_text(
            f"✅ Budget bulanan '{sub_cat_input}' berhasil diubah menjadi Rp {nominal:,}"
        )

    except Exception as e:
        await update.message.reply_text(f"Error edit budget: {str(e)}")

async def hapus_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed_user(update, context):
        return

    if not context.args:
        await update.message.reply_text(
            "Format:\n"
            "/hapusbudget <sub-kategori>\n\n"
            "Contoh:\n"
            "/hapusbudget Makan"
        )
        return

    try:
        sub_cat_input = " ".join(context.args).strip()

        budget_sheet = get_budget_sheet()
        existing = budget_sheet.get_all_values()[1:]

        row_to_delete = None
        for idx, row in enumerate(existing, start=2):
            if len(row) >= 2 and row[0].strip().lower() == sub_cat_input.lower():
                row_to_delete = idx
                break

        if not row_to_delete:
            await update.message.reply_text(
                f"Budget untuk '{sub_cat_input}' tidak ditemukan."
            )
            return

        # Hapus baris
        budget_sheet.delete_rows(row_to_delete)
        await update.message.reply_text(
            f"✅ Budget '{sub_cat_input}' berhasil dihapus dari daftar."
        )

    except Exception as e:
        await update.message.reply_text(f"Error hapus budget: {str(e)}")

# ================= HANDLE PESAN UTAMA =================
# (sudah OK, tapi timezone diubah ke zoneinfo)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_allowed_user(update, context):
        return

    user_id = update.effective_user.id
    text = update.message.text.strip()
    text_lower = text.lower()
    user_name = update.effective_user.first_name or "User"

    # Cek konfirmasi hapus dulu
    state = hapus_pending.get(user_id)
    if state:
        if time.time() - state['timestamp'] > 30:
            hapus_pending.pop(user_id, None)
            await update.message.reply_text("Konfirmasi hapus kadaluarsa.")
            return

        if text_lower in ["ya", "y", "yes"]:
            try:
                sheet = get_current_year_sheet()
                sheet.delete_rows(state['row'])
                await update.message.reply_text(f"✅ Baris {state['row']} berhasil dihapus!")
            except Exception as e:
                await update.message.reply_text(f"Gagal hapus: {str(e)}")
        else:
            await update.message.reply_text("Dibatalkan bro 😎")

        hapus_pending.pop(user_id, None)
        return
    # Setelah blok if state: (hapus_pending)
    state_edit = edit_pending.get(user_id)
    if state_edit:
        await handle_edit_reply(update, context)
        return

    # Proses transaksi / transfer
    parts = text_lower.split()
    nominal = None
    for p in parts:
        try:
            nominal = parse_nominal(p)
            break
        except:
            continue

    if nominal is None:
        await update.message.reply_text("Nominal tidak terbaca. Contoh: 50rb, 1jt, 75000")
        return

    wib = ZoneInfo("Asia/Jakarta")
    tanggal = datetime.now(wib).strftime("%Y-%m-%d %H:%M:%S")

    # TRANSFER
    if text_lower.startswith("transfer"):
        try:
            from_acc = parts[1].upper()
            ke_idx = parts.index("ke")
            to_acc = parts[ke_idx + 1].upper()

            if not account_exists(from_acc) or not account_exists(to_acc):
                await update.message.reply_text("Akun sumber atau tujuan tidak ditemukan.")
                return

            saldo_now = get_current_balance(from_acc)
            if saldo_now < nominal:
                await update.message.reply_text(f"Saldo {from_acc} kurang (saat ini: Rp {saldo_now:,})")
                return

            tanggal = datetime.now(wib).strftime("%Y-%m-%d %H:%M:%S")
            sheet = get_current_year_sheet()

            # Debit
            sheet.append_row([
                tanggal, user_name, from_acc,
                "Expenses", "Transfer", "Transfer Out",
                nominal, f"Transfer ke {to_acc}"
            ])

            # Kredit
            sheet.append_row([
                tanggal, user_name, to_acc,
                "Income", "Transfer", "Transfer In",
                nominal, f"Transfer dari {from_acc}"
            ])
            
            #update_account_balance(from_acc, get_current_balance(from_acc))
            #update_account_balance(to_acc, get_current_balance(to_acc))

            await update.message.reply_text(
                f"✅ Transfer berhasil!\n"
                f"Dari: {from_acc} → Ke: {to_acc}\n"
                f"Nominal: Rp {nominal:,}"
            )
            return

        except Exception as e:
            await update.message.reply_text(f"Format transfer salah atau error: {str(e)}")
            return

    # TRANSAKSI BIASA
    account = None
    for p in parts:
        if account_exists(p.upper()):
            account = p.upper()
            break

    if not account:
        await update.message.reply_text("Akun tidak ditemukan di sheet Account.")
        return

    categories = load_categories()
    if not categories:
        await update.message.reply_text("Sheet Categories kosong.")
        return

    # ================= PENCARIAN KATEGORI DENGAN KEYWORDS SHEET =================
    text_lower_norm = text_lower.replace("&", " ").replace("_", " ").replace("-", " ")
    text_lower_norm = " ".join(text_lower_norm.split())  # normalisasi spasi

    best_cat = None
    best_score = 0.0
    matched_keyword = None

    # Prioritas 1: Cocokkan keyword dari sheet Keywords (paling kuat)
    for sub_lower, keywords in keyword_mapping.items():
        for kw in keywords:
            if kw in text_lower_norm:
                # Cari sub yang cocok di categories
                for cat in categories:
                    if cat["sub"].strip().lower() == sub_lower:
                        best_cat = cat
                        best_score = 1.0
                        matched_keyword = kw
                        break
                if best_cat:
                    break
        if best_cat:
            break

    # Prioritas 2: Jika belum ketemu, coba partial match seperti sebelumnya
    if not best_cat:
        for cat in categories:
            sub_lower = cat["sub"].lower()
            sub_norm = sub_lower.replace("&", " ").replace("_", " ").replace("-", " ")
            sub_norm = " ".join(sub_norm.split())

            # Cek apakah ada kata sub yang muncul di pesan
            if any(word in text_lower_norm for word in sub_norm.split()) or \
               any(word in sub_norm for word in text_lower_norm.split()):
                best_cat = cat
                best_score = 0.9
                break

    # Prioritas 3: Fuzzy sebagai cadangan terakhir
    if not best_cat or best_score < 0.75:
        for cat in categories:
            sub_lower = cat["sub"].lower()
            score = difflib.SequenceMatcher(None, text_lower_norm, sub_lower).ratio()
            if score > best_score and score >= 0.68:
                best_score = score
                best_cat = cat

    if best_cat is None:
        await update.message.reply_text(
            "Maaf bro, bot gak nemu kategori yang cocok sama sekali.\n\n"
            "Contoh input yang dikenali:\n"
            "• BCA makan 25rb\n"
            "• gopay jajan 15rb\n"
            "• cash parkir 10rb\n"
            "• spbank gaji 5jt\n\n"
            "Pastikan pakai kata kunci yang sudah terdaftar di sheet Keywords atau Categories.\n"
            "Kalau belum ada, owner bisa tambah via /tambahkategori"
        )
        return

    # Lanjut proses seperti biasa kalau ketemu
    # (cek saldo, append ke sheet, balas sukses, dll)

    if best_cat["type"] == "Expenses":
        saldo_now = get_current_balance(account)
        if saldo_now < nominal:
            await update.message.reply_text(f"Saldo {account} kurang (saat ini: Rp {saldo_now:,})")
            return

    tanggal = datetime.now(wib).strftime("%Y-%m-%d %H:%M:%S")
    sheet = get_current_year_sheet()

    sheet.append_row([
        tanggal,
        user_name,
        account,
        best_cat["type"],
        best_cat["parent"],
        best_cat["sub"],
        nominal,
        text
    ])

    new_balance = get_current_balance(account)
    #update_account_balance(account, new_balance)

    tipe_display = "Pemasukan" if best_cat["type"] == "Income" else "Pengeluaran"

    await update.message.reply_text(
        f"✅ Transaksi tercatat!\n\n"
        f"Akun     : {account}\n"
        f"Tipe     : {tipe_display}\n"
        f"Nominal  : Rp {nominal:,}\n"
        f"Kategori : {best_cat['sub']}\n"
        f"Saldo sekarang: Rp {new_balance:,}"
    )

async def recurring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        await update.message.reply_text("Command ini hanya untuk owner.")
        return

    await update.message.reply_text(
        "Manajemen Recurring (masih manual via sheet 'Recurring'):\n\n"
        "1. Buka Google Sheet → sheet 'Recurring'\n"
        "2. Tambah/edit baris baru dengan format:\n"
        "   ID | Akun | Nominal | Tipe | Parent | Sub | Deskripsi | Frekuensi | Hari/Tanggal | Aktif\n"
        "Contoh:\n"
        "   5 | BCA | 150000 | Expenses | Fixed Expenses | Internet | IndiHome | monthly | 10 | Yes\n\n"
        "Bot akan otomatis proses setiap hari jam 00:05 WIB.\n"
        "Untuk sekarang belum ada command tambah/hapus via chat (bisa ditambah nanti kalau perlu)."
    )


# ================= APP SETUP =================

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("saldo", saldo))
app.add_handler(CommandHandler("chart", chart))
app.add_handler(CommandHandler("hapus", hapus))
app.add_handler(CommandHandler("edit", edit_transaksi))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(CommandHandler("menu", help_command))
app.add_handler(CommandHandler("laporan", laporan))
app.add_handler(CommandHandler("ringkasan", ringkasan))
app.add_handler(CommandHandler("riwayat", riwayat))
app.add_handler(CommandHandler("history", riwayat))
app.add_handler(CommandHandler("recent", recent_transactions))
app.add_handler(CommandHandler("riwayatterakhir", recent_transactions))  # alias kalau mau
app.add_handler(CommandHandler("kategori", kategori_riwayat))  # override /kategori yang lama kalau mau, atau pakai /riwayatkategori
app.add_handler(CommandHandler("export", export))
app.add_handler(CommandHandler("reloaduser", reloaduser))
app.add_handler(CommandHandler("kategori", daftar_kategori))
app.add_handler(CommandHandler("daftarkategori", daftar_kategori))  # alias
app.add_handler(CommandHandler("tambahkategori", tambah_kategori))
app.add_handler(CommandHandler("editkategori", edit_kategori))
app.add_handler(CommandHandler("hapuskategori", hapus_kategori))
app.add_handler(CommandHandler("recurring", recurring))
app.add_handler(CommandHandler("addrecurring", add_recurring))
app.add_handler(CommandHandler("listrecurring", list_recurring))
app.add_handler(CommandHandler("togglerecurring", toggle_recurring))
app.add_handler(CommandHandler("deleterecurring", delete_recurring))
app.add_handler(CommandHandler("setbudget", set_budget))
app.add_handler(CommandHandler("budget", budget_status))
app.add_handler(CommandHandler("budgetstatus", budget_status))  # alias kalau mau
app.add_handler(CommandHandler("editbudget", edit_budget))
app.add_handler(CommandHandler("hapusbudget", hapus_budget))

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# ================= RECURRING TRANSAKSI DENGAN JOB QUEUE =================

async def process_recurring(context: ContextTypes.DEFAULT_TYPE):
    try:
        print(f"Memproses recurring sheet: {len(data)} baris ditemukan")
        recurring_sheet = spreadsheet.worksheet("Recurring")
        data = recurring_sheet.get_all_values()[1:]  # skip header
        
        wib = ZoneInfo("Asia/Jakarta")
        today = datetime.now(wib)
        today_day = today.day
        today_weekday = today.strftime("%A").lower()  # monday, tuesday, ...
        # Cek akhir bulan sederhana
        last_day_of_month = (today.replace(day=28) + timedelta(days=4)).day
        is_last_day = today_day == last_day_of_month

        categories = load_categories()
        year_sheet = get_current_year_sheet()
        user_name = "SYSTEM_AUTO"

        for row in data:
            if len(row) < 10 or row[9].strip().lower() != "yes":
                continue

            akun = row[1].upper()
            nominal_str = row[2]
            tipe = row[3]
            parent = row[4]
            sub = row[5]
            deskripsi = row[6]
            frekuensi = row[7].lower()
            jadwal = row[8].lower()

            try:
                nominal = parse_nominal(nominal_str)
            except:
                continue

            should_process = False

            if frekuensi == "daily":
                should_process = True
            elif frekuensi == "weekly" and jadwal in ["senin","selasa","rabu","kamis","jumat","sabtu","minggu"]:
                should_process = (today_weekday == jadwal)
            elif frekuensi == "monthly":
                if jadwal == "last_day":
                    should_process = is_last_day
                else:
                    try:
                        target_day = int(jadwal)
                        should_process = (today_day == target_day)
                    except:
                        pass

            if should_process:
                tanggal = datetime.now(wib).strftime("%Y-%m-%d %H:%M:%S")
                
                cat_found = next((c for c in categories if c["sub"] == sub and c["parent"] == parent and c["type"] == tipe), None)
                if not cat_found:
                    continue

                year_sheet.append_row([
                    tanggal, user_name, akun,
                    tipe, parent, sub, nominal,
                    f"[RECURRING AUTO] {deskripsi}"
                ])

                #update_account_balance(akun, get_current_balance(akun))

                print(f"Recurring diproses: {deskripsi} - Rp {nominal:,} ke {akun}")

    except Exception as e:
        print(f"Error recurring job: {e}")

async def send_daily_summary(context: ContextTypes.DEFAULT_TYPE):
    try:
        year = datetime.now(wib).strftime("%Y")
        year_sheet = get_transaksi_sheet_by_year(year)
        data = year_sheet.get_all_values()[1:]
        if not data:
            return

        today = datetime.now(wib).strftime("%Y-%m-%d")
        daily_income = daily_expense = 0

        has_transaction = False

        for row in data:
            if len(row) < 7:
                continue
            date_str = row[0][:10]
            tipe = row[3]
            amount = parse_sheet_amount(row[6])

            if date_str != today:
                continue

            has_transaction = True

            if tipe == "Income":
                daily_income += amount
            else:
                daily_expense += amount

        if not has_transaction:
            return  # tidak kirim kalau hari ini kosong

        net = daily_income - daily_expense

        message = f"🔔 **Ringkasan Harian {today}**\n\n"
        message += f"💰 Pemasukan: Rp {daily_income:,}\n"
        message += f"💸 Pengeluaran: Rp {daily_expense:,}\n"
        message += f"📊 Net: Rp {net:,}\n\n"
        message += "Tetap semangat kelola keuangan ya bro! 🔥 Cek /saldo atau /riwayat kalau perlu detail."

        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=message,
            parse_mode="Markdown"
        )

        print(f"Ringkasan harian {today} dikirim ke owner")

    except Exception as e:
        print(f"Error kirim ringkasan harian: {e}")

async def send_monthly_report(context: ContextTypes.DEFAULT_TYPE):
    try:
        today = datetime.now(wib)
        if today.day != (today.replace(day=28) + timedelta(days=4)).day:  # hanya jalankan di akhir bulan
            return

        year = today.strftime("%Y")
        month = today.strftime("%Y-%m")
        year_sheet = get_transaksi_sheet_by_year(year)
        data = year_sheet.get_all_values()[1:]
        if not data:
            return

        monthly_income = monthly_expense = 0
        category_expenses = defaultdict(int)

        for row in data:
            if len(row) < 7:
                continue
            date_str = row[0][:7]  # YYYY-MM
            tipe = row[3]
            category = row[5].strip()
            amount = parse_sheet_amount(row[6])

            if date_str != month:
                continue

            if tipe == "Income":
                monthly_income += amount
            else:
                monthly_expense += amount
                if "Transfer" not in category:  # skip transfer seperti di chart
                    category_expenses[category] += amount

        net = monthly_income - monthly_expense

        # Top 5 pengeluaran
        top_expenses = sorted(category_expenses.items(), key=lambda x: x[1], reverse=True)[:5]
        top_text = "\n".join([f"• {cat}: Rp {amt:,}" for cat, amt in top_expenses]) or "Belum ada pengeluaran signifikan"

        message = f"📅 **Laporan Bulanan {today.strftime('%B %Y')}**\n\n"
        message += f"💰 Total Pemasukan: Rp {monthly_income:,}\n"
        message += f"💸 Total Pengeluaran: Rp {monthly_expense:,}\n"
        message += f"📊 Net: Rp {net:,}\n\n"
        message += f"**Top 5 Pengeluaran Bulan Ini:**\n{top_text}\n\n"
        message += "Tetap konsisten ya bro! Cek /laporan atau /chart kalau mau detail lebih lanjut. 🔥"

        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=message,
            parse_mode="Markdown"
        )

        print(f"Laporan bulanan {month} dikirim ke owner")

    except Exception as e:
        print(f"Error kirim laporan bulanan: {e}")

# Backup mingguan tiap Minggu jam 23:00 WIB (pakai run_daily + cek hari)
async def weekly_backup(context: ContextTypes.DEFAULT_TYPE):
    try:
        today = datetime.now(wib)
        if today.weekday() != 6:  # 0=Senin, 6=Minggu
            return  # bukan Minggu → skip

        sheet = get_current_year_sheet()
        data = sheet.get_all_values()
        if len(data) <= 1:
            return

        filename = f"backup_mingguan_{today.strftime('%Y%m%d')}.csv"
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerows(data)

        await context.bot.send_document(
            chat_id=OWNER_ID,
            document=open(filename, 'rb'),
            caption=f"Backup mingguan semua transaksi ({today.strftime('%d %B %Y')}) - otomatis"
        )

        os.remove(filename)
        print("Backup mingguan dikirim")

    except Exception as e:
        print(f"Error backup mingguan: {e}")

# Jadwalkan job setiap hari jam 00:05 WIB
job_queue = app.job_queue
if job_queue:
    job_queue.run_daily(
        process_recurring,
        time=dt_time(hour=0, minute=5, second=0, tzinfo=ZoneInfo("Asia/Jakarta"))
    )
    print("Job recurring harian dijadwalkan jam 00:05 WIB")
else:
    print("WARNING: Job queue tidak tersedia!")

# Jadwalkan ringkasan harian jam 21:00 WIB
job_queue.run_daily(
    send_daily_summary,
    time=dt_time(hour=21, minute=0, second=0, tzinfo=wib)
)
print("Ringkasan harian otomatis dijadwalkan jam 21:00 WIB")

# Jadwalkan cek akhir bulan setiap hari jam 23:59 WIB
job_queue.run_daily(
    send_monthly_report,
    time=dt_time(hour=23, minute=59, second=0, tzinfo=wib)
)
print("Cek laporan bulanan otomatis dijadwalkan setiap hari jam 23:59 WIB")

# Jadwalkan cek backup setiap hari jam 23:00 WIB
job_queue.run_daily(
    weekly_backup,
    time=dt_time(hour=23, minute=0, second=0, tzinfo=wib)
)
print("Cek backup mingguan otomatis dijadwalkan setiap hari jam 23:00 WIB (jalan hanya di hari Minggu)")

if __name__ == "__main__":
    load_allowed_users_sync()

    base_url = WEBHOOK_URL.rstrip('/')
    webhook_url = f"{base_url}/{TOKEN}"

    print("Setting webhook →", webhook_url)

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=webhook_url,
        drop_pending_updates=True
    )