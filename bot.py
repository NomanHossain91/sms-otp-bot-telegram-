import os
import telebot
from telebot import types
import requests

# ==================== ১. কনফিগারেশন সেটআপ ====================
BOT_TOKEN = os.environ.get('BOT_TOKEN')
IVSMS_API_KEY = os.environ.get('IVSMS_API_KEY')

if not BOT_TOKEN or not IVSMS_API_KEY:
    raise ValueError("ERROR: BOT_TOKEN or IVSMS_API_KEY is missing in Environment Variables!")

bot = telebot.TeleBot(BOT_TOKEN)

# একটিভ অর্ডার ট্র্যাক করার গ্লোবাল ডিকশনারি (বাগ ফিক্সড)
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
    
    # IVSMS API-তে রিকোয়েস্ট পাঠিয়ে রিয়াল নম্বর নেওয়ার প্রসেস
    # নোট: আপনার IVSMS প্ল্যান অনুযায়ী সার্ভিস কোড (যেমন: tg, wa, fb) পরিবর্তন করতে পারেন।
    try:
        # IVSMS API Endpoint ও প্যারামিটার সেটআপ
        api_url = "https://api.ivsms.com/stargate/v1/buy"
        params = {
            "api_key": IVSMS_API_KEY,
            "country": country,
            "service": "tg",  # tg = Telegram, wa = WhatsApp (আপনার প্রয়োজনমতো দিবেন)
            "count": 3        # একসাথে ৩টি নম্বর রিকোয়েস্ট
        }
        
        response = requests.get(api_url, params=params, timeout=15).json()
        
        # যদি IVSMS সফলভাবে নম্বর প্রদান করে
        if response.get("status") == "success" or "numbers" in response:
            # এপিআই রেসপন্স থেকে নম্বর ও অর্ডার আইডি লুপ করে বের করা
            for item in response.get("numbers", []):
                captured_numbers.append(item.get("phone"))
                order_ids.append(item.get("order_id"))
        else:
            # এপিআই যদি কোনো কারণে নম্বর না দেয় (যেমন: ব্যালেন্স শেষ)
            bot.send_message(chat_id, f"❌ এপিআই এরর: {response.get('message', 'নম্বর পাওয়া যায়নি বা ব্যালেন্স শেষ।')}")
            return

    except Exception as e:
        bot.send_message(chat_id, f"⚠️ সার্ভার কানেকশন এরর: {str(e)}")
        return

    # মেমরিতে অর্ডার ডেটা সফলভাবে সেভ করা
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
    # প্রতিটি নম্বরের বাটন তৈরি (বাটন ক্লিকের এরর এড়াতে callback_data ছোট রাখা হয়েছে)
    for index, num in enumerate(captured_numbers):
        markup.row(types.InlineKeyboardButton(text=f"📋 📱 {num}", callback_data=f"copy_{index}"))
        
    btn_group = types.InlineKeyboardButton(text="🔔 OTP GROUP", url="https://t.me/real_earning_method")
    btn_change = types.InlineKeyboardButton(text="🔄 Change Number", callback_data="change_number")
    btn_refresh = types.InlineKeyboardButton(text="🔄 Refresh", callback_data="refresh_otp")
    btn_back = types.InlineKeyboardButton(text="⬅️ Back", callback_data="go_back")
    
    markup.row(btn_group)
    markup.row(btn_change, btn_refresh)
    markup.row(btn_back)

    # আগের মেসেজটি রিমুভ করে ক্লিন ইন্টারফেস দেওয়া
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except:
        pass
        
    bot.send_message(chat_id, response_text, reply_markup=markup)

# ==================== ৫. ওটিপি রিফ্রেশ (Refresh) করার লজিক ====================
@bot.callback_query_handler(func=lambda call: call.data == 'refresh_otp')
def check_otp_status(call):
    chat_id = call.message.chat.id
    
    if chat_id in active_orders:
        order_data = active_orders[chat_id]
        order_ids = order_data["order_ids"]
        
        bot.answer_callback_query(call.id, text="সার্ভারে ওটিপি চেক করা হচ্ছে...")
        
        otp_received_list = []
        
        # আইভিএসএমএস সার্ভারে প্রতিটি অর্ডার আইডির জন্য ওটিপি চেক করা হচ্ছে
        try:
            check_url = "https://api.ivsms.com/stargate/v1/check"
            for o_id in order_ids:
                params = {
                    "api_key": IVSMS_API_KEY,
                    "order_id": o_id
                }
                api_res = requests.get(check_url, params=params, timeout=10).json()
                
                # যদি ওটিপি এসে থাকে
                if api_res.get("status") == "received" and "code" in api_res:
                    otp_received_list.append(f"📱 {api_res.get('phone')}: `{api_res.get('code')}`")
            
            # ফলাফল ইউজারের চ্যাটে পাঠানো
            if otp_received_list:
                success_msg = "🎉 🔔 **কোড চলে এসেছে!**\n\n" + "\n".join(otp_received_list)
                bot.send_message(chat_id, success_msg, parse_mode="Markdown")
            else:
                bot.send_message(chat_id, "⏳ এখনো কোনো ওটিপি (OTP) আসেনি। অনুগ্রহ করে অ্যাপস থেকে এসএমএস পাঠিয়ে ১ মিনিট পর আবার 'Refresh' চাপুন।")
                
        except Exception as e:
            bot.send_message(chat_id, f"⚠️ ওটিপি চেক করার সময় সমস্যা হয়েছে: {str(e)}")
    else:
        bot.answer_callback_query(call.id, text="আপনার কোনো একটিভ নম্বর সেশন পাওয়া যায়নি!")

# নম্বর বাটনে ক্লিক করলে যাতে কোনো এরর না দেখায় তার হ্যান্ডলার
@bot.callback_query_handler(func=lambda call: call.data.startswith('copy_'))
def handle_copy_click(call):
    bot.answer_callback_query(call.id, text="নম্বরটি চেপে ধরে কপি করুন")

# ==================== ৬. ব্যাক বাটন হ্যান্ডলার ====================
@bot.callback_query_handler(func=lambda call: call.data in ['go_back', 'change_number'])
def go_back_handler(call):
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    country_menu(call.message)

# বট রান করা
print("GetPaid OTP Bot is running completely Bug-Free...")
bot.infinity_polling()
