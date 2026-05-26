import os
import telebot
from telebot import types
import requests
import time

# ==================== ১. কনফিগারেশন সেটআপ (Railway এনভায়রনমেন্ট থেকে নেবে) ====================
BOT_TOKEN = os.environ.get('BOT_TOKEN')
IVSMS_API_KEY = os.environ.get('IVSMS_API_KEY')

if not BOT_TOKEN:
    raise ValueError("Error: BOT_TOKEN environment variable is missing!")

bot = telebot.TeleBot(BOT_TOKEN)

# একটিভ অর্ডার ট্র্যাক করার মেমোরি ডাটাবেজ
active_orders = {}

# ==================== ২. স্টার্ট কমান্ড ও মেইন মেনু ====================
@bot.message_handler(commands=['start'])
def welcome_message(message):
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    btn1 = types.KeyboardButton('☎️ Get Number')
    btn2 = types.KeyboardButton('📨 Get Tempmail')
    btn3 = types.KeyboardButton('🔐 2FA')
    btn4 = types.KeyboardButton('👤 Fake Name')
    markup.add(btn1, btn2, btn3, btn4)
    
    bot.send_message(
        message.chat.id, 
        f"👋 Welcome back to GetPaid OTP 2.0 Bot\nYour ID: `{message.chat.id}`", 
        reply_markup=markup,
        parse_mode="Markdown"
    )

# ==================== ৩. দেশ সিলেক্ট করার মেনু ====================
@bot.message_handler(func=lambda message: message.text == '☎️ Get Number')
def country_menu(message):
    markup = types.InlineKeyboardMarkup()
    btn_peru = types.InlineKeyboardButton("🇵🇪 Peru 🔥 (0.20TK) 📦 32136", callback_data="buy_peru")
    btn_myanmar = types.InlineKeyboardButton("🇲🇲 Myanmar (0.20TK) 📦 4183", callback_data="buy_myanmar")
    markup.add(btn_peru)
    markup.add(btn_myanmar)
    
    bot.send_message(message.chat.id, "🌍 নিচে থেকে আপনার পছন্দের দেশটি সিলেক্ট করুন:", reply_markup=markup)

# ==================== ৪. নম্বর কেনা এবং স্ক্রিনে দেখানোর লজিক ====================
@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_'))
def process_number_purchase(call):
    chat_id = call.message.chat.id
    country = call.data.split('_')[1] 
    
    bot.answer_callback_query(call.id, text="সার্ভার থেকে নম্বর নেওয়া হচ্ছে, অপেক্ষা করুন...")

    captured_numbers = []
    order_ids = []
    
    # ৩টি লাইভ নম্বর লুপের মাধ্যমে নেওয়ার স্ট্রাকচার
    for i in range(3):
        # 🔗 তোমার IVSMS এপিআই লিংক এখানে বসাবে:
        # Example: api_url = f"https://api.ivsms.com/stargate/v1/buy?api_key={IVSMS_API_KEY}&country={country}&service=instagram"
        # response = requests.get(api_url).json()
        
        # টেস্টিংয়ের জন্য কাল্পনিক ডেমো নম্বর (এপিআই কানেক্ট করলে এগুলো রিয়াল আসবে)
        if i == 0: fake_num = "51918348393"
        elif i == 1: fake_num = "51935860294"
        else: fake_num = "51931911948"
        
        captured_numbers.append(fake_num)
        order_ids.append(f"ID_{country}_{i}_{int(time.time())}")

    active_orders[chat_id] = {
        "numbers": captured_numbers,
        "order_ids": order_ids,
        "country": country
    }

    response_text = (
        "┌─── NUMBER VERIFIED SUCCESSFULLY ───┐\n\n"
        f">> 🇵🇪 {country.capitalize()} 🔥 (0.20TK) 📦 32136\n\n"
        "└─── NUMBER VERIFIED SUCCESSFULLY ───┘"
    )

    markup = types.InlineKeyboardMarkup()
    for num in captured_numbers:
        markup.row(types.InlineKeyboardButton(text=f"📋 📱 {num}", callback_data=f"none_{num}"))
        
    btn_group = types.InlineKeyboardButton(text="🔔 OTP GROUP", url="https://t.me/real_earning_method")
    btn_change = types.InlineKeyboardButton(text="🔄 Change Number", callback_data="change_number")
    btn_refresh = types.InlineKeyboardButton(text="🔄 Refresh", callback_data="refresh_otp")
    btn_back = types.InlineKeyboardButton(text="⬅️ Back", callback_data="go_back")
    
    markup.row(btn_group)
    markup.row(btn_change, btn_refresh)
    markup.row(btn_back)

    bot.send_message(chat_id, response_text, reply_markup=markup)

# ==================== ৫. ওটিপি রিফ্রেশ (Refresh) করার লজিক ====================
@bot.callback_query_handler(func=lambda call: call.data == 'refresh_otp')
def check_otp_status(call):
    chat_id = call.message.chat.id
    
    if chat_id in active_orders:
        bot.answer_callback_query(call.id, text="সার্ভারে ওটিপি চেক করা হচ্ছে...")
        
        # 🔗 এখানে IVSMS এর ওটিপি চেক করার এপিআই কল লজিক বসবে
        otp_found = False
        
        if otp_found:
            received_code = "123456" 
            bot.send_message(chat_id, f"📩 **আপনার ওটিপি কোডটি হলো:** `{received_code}`", parse_mode="Markdown")
        else:
            bot.send_message(chat_id, "⏳ এখনো কোনো ওটিপি (OTP) আসেনি। অনুগ্রহ করে অ্যাপস থেকে এসএমএস পাঠিয়ে আবার 'Refresh' চাপুন।")
    else:
        bot.answer_callback_query(call.id, text="আপনার কোনো একটিভ নম্বর সেশন পাওয়া যায়নি!")

# ==================== ৬. ব্যাক বাটন হ্যান্ডলার ====================
@bot.callback_query_handler(func=lambda call: call.data in ['go_back', 'change_number'])
def go_back_handler(call):
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    country_menu(call.message)

print("GetPaid OTP Bot is running successfully...")
bot.infinity_polling()
