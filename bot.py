# bot.py
import os
import logging
from datetime import datetime
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from dotenv import load_dotenv

from database import (
    create_order, get_order, mark_verified,
    record_fee, update_order_with_fee,
)
from binance_pay import get_binance_pay_client, find_transaction_by_order_id

load_dotenv()

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
YOUR_BINANCE_PAY_ID = os.getenv("YOUR_BINANCE_PAY_ID", "1240241584")
FEE_RATE = float(os.getenv("FEE_RATE", 0.05))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

binance_client = get_binance_pay_client(BINANCE_API_KEY, BINANCE_API_SECRET)

# Conversation states
CHOOSING_AMOUNT, CUSTOM_AMOUNT, WAITING_ADDRESS, CONFIRMING, WAITING_ORDER_ID = range(5)

# প্রিসেট অ্যামাউন্ট (ইউজার চাইলে বাটন থেকে বেছে নিতে পারবে)
PRESET_AMOUNTS = [0.50, 1.00, 1.50, 2.00, 4.00]

# মিনিমাম ও ম্যাক্সিমাম কাস্টম অ্যামাউন্ট
MIN_CUSTOM_AMOUNT = 0.50
MAX_CUSTOM_AMOUNT = 1000.00


# ============ Helpers ============

def cancel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])


def short_address(addr: str) -> str:
    if len(addr) <= 10:
        return addr
    return f"{addr[:6]}…{addr[-4:]}"


# ============ /start ============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Order", callback_data="order")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ])
    await update.message.reply_text(
        "👋 **স্বাগতম!**\n\n"
        "USDT ডিপোজিট করতে নিচের **🛒 Order** বাটনে ক্লিক করুন।",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


# ============ Order Button ============

async def order_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    lines = [
        "💵 **কত USDT পেতে চান?**",
        "───────────────",
        "",
        f"প্রতি ট্রানজেকশনে ফি: `{FEE_RATE} USDT`",
        "",
        "**উপলব্ধ অ্যামাউন্ট:**",
    ]
    for amt in PRESET_AMOUNTS:
        total = round(amt + FEE_RATE, 6)
        lines.append(f"├ {amt:.2f} USDT → {total:.2f} USDT পাঠাতে হবে।")
    lines.append("")
    lines.append("**নিচের অপশন নির্বাচন করুন, অথবা যেকোনো অ্যামাউন্ট নিজে টাইপ করুন:**")

    buttons = []
    row = []
    for amt in PRESET_AMOUNTS:
        row.append(InlineKeyboardButton(f"{amt:.2f}", callback_data=f"amt_{amt:.2f}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton("✏️ Custom Amount", callback_data="custom")])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return CHOOSING_AMOUNT


# ============ Amount Selected (Preset) ============

async def amount_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        net_amount = float(query.data.split("_")[1])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ ভুল অ্যামাউন্ট।")
        return ConversationHandler.END

    return await _ask_address(query, context, net_amount)


# ============ Custom Amount ============

async def custom_amount_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        f"✏️ **কাস্টম অ্যামাউন্ট**\n"
        f"───────────────\n\n"
        f"আপনি কত **USDT** পেতে চান, শুধু সংখ্যাটি লিখুন।\n\n"
        f"**উদাহরণ:**\n"
        f"`0.75` — 0.75 USDT পেতে\n"
        f"`5` — 5 USDT পেতে\n"
        f"`12.50` — 12.50 USDT পেতে\n\n"
        f"**সীমা:** সর্বনিম্ন `{MIN_CUSTOM_AMOUNT} USDT`, সর্বোচ্চ `{MAX_CUSTOM_AMOUNT} USDT`\n\n"
        f"বাতিল করতে /cancel দিন।",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard(),
    )
    return CUSTOM_AMOUNT


async def custom_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(",", ".")

    try:
        net_amount = float(text)
    except ValueError:
        await update.message.reply_text(
            "❌ **ভুল ফরম্যাট।** শুধু সংখ্যা লিখুন।\n"
            "উদাহরণ: `0.75` অথবা `5`",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard(),
        )
        return CUSTOM_AMOUNT

    if net_amount < MIN_CUSTOM_AMOUNT:
        await update.message.reply_text(
            f"❌ সর্বনিম্ন অ্যামাউন্ট `{MIN_CUSTOM_AMOUNT} USDT`।\n"
            f"আবার লিখুন অথবা /cancel দিন।",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard(),
        )
        return CUSTOM_AMOUNT

    if net_amount > MAX_CUSTOM_AMOUNT:
        await update.message.reply_text(
            f"❌ সর্বোচ্চ অ্যামাউন্ট `{MAX_CUSTOM_AMOUNT} USDT`।\n"
            f"আবার লিখুন অথবা /cancel দিন।",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard(),
        )
        return CUSTOM_AMOUNT

    # সংখ্যাটি ঠিক আছে, এখন address চাই
    net_amount = round(net_amount, 6)
    total_amount = round(net_amount + FEE_RATE, 6)
    context.user_data["net_amount"] = net_amount
    context.user_data["total_amount"] = total_amount

    await update.message.reply_text(
        f"💳 **BSC Network Address পাঠান**\n"
        f"───────────────\n\n"
        f"আপনি পাবেন  : `{net_amount:.2f} USDT`\n"
        f"পাঠাতে হবে  : `{total_amount:.2f} USDT`\n\n"
        f"আপনার **BSC Network (BEP20)** Address দিন!\n"
        f"উদাহরণ: `0x1234abcd...`\n\n"
        f"⚠️ ভুল Address দিলে এডমিন দায়ী নয়।\n\n"
        f"বাতিল করতে /cancel দিন।",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard(),
    )
    return WAITING_ADDRESS


# ============ Common: Ask Address ============

async def _ask_address(query_or_update, context: ContextTypes.DEFAULT_TYPE, net_amount: float):
    """প্রিসেট অ্যামাউন্ট সিলেক্ট করার পর address চাওয়া।"""
    total_amount = round(net_amount + FEE_RATE, 6)
    context.user_data["net_amount"] = net_amount
    context.user_data["total_amount"] = total_amount

    if hasattr(query_or_update, "edit_message_text"):
        await query_or_update.edit_message_text(
            f"💳 **BSC Network Address পাঠান**\n"
            f"───────────────\n\n"
            f"আপনি পাবেন  : `{net_amount:.2f} USDT`\n"
            f"পাঠাতে হবে  : `{total_amount:.2f} USDT`\n\n"
            f"আপনার **BSC Network (BEP20)** Address দিন!\n"
            f"উদাহরণ: `0x1234abcd...`\n\n"
            f"⚠️ ভুল Address দিলে এডমিন দায়ী নয়।\n\n"
            f"বাতিল করতে /cancel দিন।",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard(),
        )
    return WAITING_ADDRESS


# ============ Address Received ============

async def address_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    address = update.message.text.strip()

    if not (address.startswith("0x") and len(address) == 42):
        await update.message.reply_text(
            "❌ **ভুল Address ফরম্যাট।**\n\n"
            "BSC (BEP20) address `0x` দিয়ে শুরু হয় এবং ৪২ অক্ষরের হয়।\n"
            "উদাহরণ: `0x1234abcd...`\n\n"
            "আবার পাঠান অথবা /cancel দিন।",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard(),
        )
        return WAITING_ADDRESS

    net_amount = context.user_data.get("net_amount")
    total_amount = context.user_data.get("total_amount")

    if not net_amount or not total_amount:
        await update.message.reply_text("⚠️ সেশন মেয়াদোত্তীর্ণ। আবার /start দিন।")
        return ConversationHandler.END

    context.user_data["address"] = address

    await update.message.reply_text(
        f"📋 **ভালো করে দেখুন!**\n"
        f"───────────────\n"
        f"পাঠাতে হবে    : `{total_amount:.2f} USDT`\n"
        f"নেটওয়ার্ক ফি : `{FEE_RATE:.2f} USDT`\n"
        f"পাবেন           : `{net_amount:.2f} USDT`\n"
        f"Address      : `{short_address(address)}`\n"
        f"নেটওয়ার্ক      : **BSC Network**\n\n"
        f"সব সঠিক?\n\n"
        f"👉 লিখুন `yes` — নিশ্চিত করতে\n"
        f"👉 লিখুন `no` — বাতিল করতে",
        parse_mode="Markdown",
    )
    return CONFIRMING


# ============ Confirm yes/no ============

async def confirm_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()

    if text == "yes":
        return await _show_payment_instructions(update, context)
    elif text == "no":
        context.user_data.clear()
        await update.message.reply_text(
            "❌ বাতিল করা হয়েছে।\n\nআবার শুরু করতে /start দিন।"
        )
        return ConversationHandler.END
    else:
        await update.message.reply_text(
            "⚠️ শুধু `yes` অথবা `no` লিখুন।",
            parse_mode="Markdown",
        )
        return CONFIRMING


# ============ Payment Instructions ============

async def _show_payment_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    net_amount = context.user_data.get("net_amount")
    total_amount = context.user_data.get("total_amount")

    if not net_amount or not total_amount:
        await update.message.reply_text("⚠️ সেশন মেয়াদোত্তীর্ণ। আবার /start দিন।")
        return ConversationHandler.END

    await update.message.reply_text(
        f"💸 **পেমেন্ট নির্দেশনা**\n"
        f"───────────────\n"
        f"১. Binance Pay খুলুন\n"
        f"২. নিচের UID কপি করুন\n\n"
        f"🟨 **Binance UID:** `{YOUR_BINANCE_PAY_ID}`\n\n"
        f"📥 আপনি পাবেন : `{net_amount:.2f} USDT`\n"
        f"💸 নেটওয়ার্ক ফি  : `{FEE_RATE:.2f} USDT`\n"
        f"💰 পাঠাতে হবে    : `{total_amount:.2f} USDT`\n\n"
        f"৩. এখানে দেখানো সঠিক পরিমাণ পাঠান।\n"
        f"৪. পেমেন্ট শেষে Binance Order ID এখানে পাঠান!\n\n"
        f"❓ **Binance Order ID কোথায় পাব?**\n"
        f"Binance অ্যাপ → Pay → History → ট্রানজেকশনে ক্লিক → Order ID কপি করুন।\n\n"
        f"⚠️ Binance Order ID বা Order ID-সহ স্ক্রিনশট কখনো পাবলিক গ্রুপে বা "
        f"অন্য কারো সাথে শেয়ার করবেন না। শুধুমাত্র এখানে পাঠান।\n"
        f"কোনো সমস্যা হলে সরাসরি এডমিনকে জানান।\n\n"
        f"বাতিল করতে /cancel দিন।",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard(),
    )
    return WAITING_ORDER_ID


# ============ Order ID Received → Verify ============

async def order_id_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    order_id = update.message.text.strip()
    user_id = update.effective_user.id

    if len(order_id) < 8 or " " in order_id:
        await update.message.reply_text(
            "❌ **Order ID সঠিক মনে হচ্ছে না।**\n\n"
            "Binance Pay History থেকে সঠিক **Order ID** কপি করে পাঠান।\n"
            "উদাহরণ: `M_P_71505104267788288`",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard(),
        )
        return WAITING_ORDER_ID

    status_msg = await update.message.reply_text(
        "⏳ **আপনার পেমেন্ট চেক করা হচ্ছে...**\n"
        "Binance Pay API থেকে ডেটা আনা হচ্ছে, অনুগ্রহ করে অপেক্ষা করুন।"
    )

    result = find_transaction_by_order_id(binance_client, order_id)

    if not result.get("found"):
        await status_msg.edit_text(
            "❌ **পেমেন্ট পাওয়া যায়নি।**\n\n"
            "সম্ভাব্য কারণ:\n"
            "• Order ID সঠিক নয়\n"
            "• পেমেন্ট এখনো কনফার্ম হয়নি (১-২ মিনিট অপেক্ষা করুন)\n"
            "• ভুল Binance অ্যাকাউন্টে পাঠানো হয়েছে\n\n"
            "সঠিক Order ID আবার পাঠান, অথবা /cancel দিন।",
            parse_mode="Markdown",
        )
        return WAITING_ORDER_ID

    amount = float(result.get("amount", 0))
    if amount <= 0:
        await status_msg.edit_text("⚠️ অ্যামাউন্ট সঠিক নয়।")
        return WAITING_ORDER_ID

    create_order(user_id, order_id, amount,
                 forward_to=context.user_data.get("address", ""))
    mark_verified(order_id, amount)

    fee = round(amount * FEE_RATE, 6)
    net_amount = round(amount - fee, 6)

    record_fee(order_id, fee)
    update_order_with_fee(order_id, fee, net_amount)

    await status_msg.edit_text(
        f"✅ **Payment Successfully!**\n"
        f"───────────────\n\n"
        f"**Order ID:** `{order_id}`\n"
        f"**মোট অ্যামাউন্ট:** `{amount} USDT`\n"
        f"**ফি:** `{fee} USDT`\n"
        f"**নেট অ্যামাউন্ট:** `{net_amount} USDT`\n"
        f"**সময়:** `{datetime.fromtimestamp(result['transactionTime'] / 1000)}`\n\n"
        f"💸 আপনার `{net_amount} USDT` ফরওয়ার্ড করা হচ্ছে...",
        parse_mode="Markdown",
    )

    await forward_payment(update, context, order_id, net_amount, fee)

    context.user_data.clear()
    return ConversationHandler.END


async def forward_payment(update, context, order_id, net_amount, fee):
    # ⬇️ এখানে আপনার আসল ফরওয়ার্ডিং লজিক বসান (BSC on-chain transfer ইত্যাদি)
    await update.message.reply_text(
        f"💸 **ফরওয়ার্ড সম্পন্ন!**\n\n"
        f"**Order ID:** `{order_id}`\n"
        f"**পাঠানো হয়েছে:** `{net_amount} USDT`\n"
        f"**ফি:** `{fee} USDT`",
        parse_mode="Markdown",
    )


# ============ /cancel ============

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "❌ বাতিল করা হয়েছে।\n\nআবার শুরু করতে /start দিন।"
        )
    else:
        await update.message.reply_text(
            "❌ বাতিল করা হয়েছে।\n\nআবার শুরু করতে /start দিন।",
            reply_markup=ReplyKeyboardRemove(),
        )
    return ConversationHandler.END


# ============ /help ============

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "**কমান্ডসমূহ:**\n\n"
        "/start — শুরু করুন\n"
        "/cancel — বাতিল করুন\n"
        "/help — সাহায্য",
        parse_mode="Markdown",
    )


# ============ Main ============

def main():
    port = int(os.environ.get("PORT", 10000))
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(order_button, pattern="^order$"),
        ],
        states={
            CHOOSING_AMOUNT: [
                CallbackQueryHandler(amount_selected, pattern=r"^amt_"),
                CallbackQueryHandler(custom_amount_prompt, pattern="^custom$"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            CUSTOM_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, custom_amount_received),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            WAITING_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, address_received),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            CONFIRMING: [
                MessageHandler(filters.Regex(r"(?i)^(yes|no)$"), confirm_order),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            WAITING_ORDER_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, order_id_received),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
        ],
        per_message=False,
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(conv_handler)

    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    if render_url:
        webhook_url = f"{render_url}/{TELEGRAM_BOT_TOKEN}"
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=TELEGRAM_BOT_TOKEN,
            webhook_url=webhook_url,
        )
    else:
        app.run_polling()


if __name__ == "__main__":
    main()
