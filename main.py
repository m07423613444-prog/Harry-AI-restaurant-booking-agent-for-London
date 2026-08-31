import os
import json
import re
from datetime import datetime

from dotenv import load_dotenv
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import Response
from openai import OpenAI
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse, Gather

load_dotenv()

# =========================
# الإعدادات
# =========================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "")
TWILIO_VOICE_NUMBER = os.getenv("TWILIO_VOICE_NUMBER", "")

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
DEFAULT_RESTAURANT_PHONE = os.getenv("DEFAULT_RESTAURANT_PHONE", "")

AI_DISCLOSURE = (
    os.getenv("AI_DISCLOSURE", "true").lower() == "true"
)

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing")

if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
    raise RuntimeError("Twilio credentials are missing")


openai_client = OpenAI(api_key=OPENAI_API_KEY)

twilio = TwilioClient(
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN
)

app = FastAPI(
    title="Harry AI Restaurant Booking Agent"
)

# =========================
# تخزين مؤقت
# =========================

bookings = {}
restaurants = {}
call_sessions = {}


# =========================
# نموذج الحجز
# =========================

class Booking:

    def __init__(
        self,
        booking_id,
        user_whatsapp,
        name,
        restaurant,
        restaurant_phone,
        guests,
        date,
        time,
        notes=""
    ):

        self.id = booking_id
        self.user_whatsapp = user_whatsapp
        self.name = name
        self.restaurant = restaurant
        self.restaurant_phone = restaurant_phone
        self.guests = guests
        self.date = date
        self.time = time
        self.notes = notes

        self.status = "pending"
        self.call_sid = None

        self.created_at = (
            datetime.utcnow().isoformat() + "Z"
        )

    def as_dict(self):
        return self.__dict__


# =========================
# تنظيف رقم الهاتف
# =========================

def normalize_phone(value: str) -> str:

    value = (value or "").strip()

    value = re.sub(
        r"[^\d+]",
        "",
        value
    )

    if value.startswith("00"):
        value = "+" + value[2:]

    return value


# =========================
# OpenAI JSON
# =========================

def ai_json(prompt: str) -> dict:

    response = openai_client.responses.create(
        model=OPENAI_MODEL,
        input=prompt,
        store=False,
    )

    text = response.output_text.strip()

    text = re.sub(
        r"^```json\s*",
        "",
        text
    )

    text = re.sub(
        r"\s*```$",
        "",
        text
    )

    return json.loads(text)


# =========================
# فهم رسالة العميل بالعربي
# =========================

def parse_arabic_request(message: str) -> dict:

    today = datetime.now().strftime(
        "%Y-%m-%d"
    )

    prompt = f"""
You are Harry, an AI restaurant booking assistant.

The customer may speak Arabic,
English, dialect, or mixed languages.

IMPORTANT:

The customer normally communicates with Harry
in Arabic.

Harry must understand Arabic naturally.

Extract only:

restaurant_name
guests
date
time
customer_name
notes

Rules:

- guests must be an integer or null.
- date must be YYYY-MM-DD or null.
- time must be HH:MM 24-hour format or null.
- Never invent information.
- If information is missing, return null.
- Resolve today/tomorrow using today's date:
  {today}

Return JSON only.

Example:

{{
  "restaurant_name": "Restaurant Name",
  "guests": 4,
  "date": "2026-09-05",
  "time": "19:30",
  "customer_name": "Ahmed",
  "notes": ""
}}

Customer message:

{message}
"""

    return ai_json(prompt)


# =========================
# رد هاري للمطعم
# =========================

def english_call_reply(
    booking: Booking,
    restaurant_said: str
) -> str:

    prompt = f"""
You are Harry.

You are an AI restaurant booking assistant
speaking on a telephone call.

IMPORTANT:

You MUST speak ENGLISH to the restaurant.

The customer communicates with Harry in Arabic,
but the restaurant conversation is always English.

Customer information:

Name:
{booking.name}

Restaurant:
{booking.restaurant}

Date:
{booking.date}

Time:
{booking.time}

Guests:
{booking.guests}

Notes:
{booking.notes or "none"}

The restaurant just said:

"{restaurant_said}"

Generate ONE short,
natural English sentence for Harry to say.

Rules:

- Speak politely.
- Sound natural.
- Keep it short.
- Try to complete the reservation.
- If the requested time is unavailable,
  ask for another available time.
- Never claim confirmation unless the restaurant
  clearly confirms the booking.
- If confirmed, thank the restaurant
  and end the call.

Return only the sentence.
"""

    response = openai_client.responses.create(
        model=OPENAI_MODEL,
        input=prompt,
        store=False,
    )

    return response.output_text.strip()


# =========================
# معرفة هل المطعم أكد الحجز
# =========================

def detect_confirmation(text: str) -> bool:

    prompt = f"""
Determine whether the restaurant clearly
CONFIRMED the reservation.

Return JSON only:

{{
  "confirmed": true
}}

or

{{
  "confirmed": false
}}

Do NOT count these as confirmation:

- We will check.
- Let me check.
- Probably.
- I can try.
- Hold on.
- I need to check.

Restaurant statement:

{text}
"""

    try:

        result = ai_json(prompt)

        return bool(
            result.get("confirmed")
        )

    except Exception:

        low = text.lower()

        phrases = [
            "confirmed",
            "reservation is confirmed",
            "booked for you",
            "table is booked",
            "you're all set",
            "you are all set"
        ]

        return any(
            phrase in low
            for phrase in phrases
        )


# =========================
# إرسال واتساب
# =========================

def send_whatsapp(
    to: str,
    body: str
):

    destination = (
        to
        if to.startswith("whatsapp:")
        else f"whatsapp:{to}"
    )

    twilio.messages.create(
        from_=f"whatsapp:{TWILIO_WHATSAPP_NUMBER}",
        to=destination,
        body=body,
    )


# =========================
# بدء مكالمة المطعم
# =========================

def start_restaurant_call(
    booking: Booking
):

    if not booking.restaurant_phone:

        booking.status = (
            "needs_restaurant_phone"
        )

        send_whatsapp(
            booking.user_whatsapp,
            "لم أجد رقم هاتف للمطعم. "
            "أرسل لي رقم المطعم وسأحاول الحجز."
        )

        return


    if not PUBLIC_BASE_URL:

        booking.status = (
            "configuration_error"
        )

        send_whatsapp(
            booking.user_whatsapp,
            "النظام يحتاج إلى PUBLIC_BASE_URL "
            "قبل إجراء المكالمة."
        )

        return


    call = twilio.calls.create(

        to=booking.restaurant_phone,

        from_=TWILIO_VOICE_NUMBER,

        url=(
            f"{PUBLIC_BASE_URL}"
            f"/voice/start"
            f"?booking_id={booking.id}"
        ),

        status_callback=(
            f"{PUBLIC_BASE_URL}"
            f"/voice/status"
            f"?booking_id={booking.id}"
        ),

        status_callback_event=[
            "initiated",
            "ringing",
            "answered",
            "completed"
        ]
    )

    booking.call_sid = call.sid

    booking.status = "calling"


# =========================
# معالجة طلب واتساب
# =========================

def process_whatsapp(
    message: str,
    from_number: str
):

    try:

        data = parse_arabic_request(
            message
        )

        restaurant_name = data.get(
            "restaurant_name"
        )

        guests = data.get("guests")

        date = data.get("date")

        time = data.get("time")

        customer_name = (
            data.get("customer_name")
            or "Customer"
        )

        notes = (
            data.get("notes")
            or ""
        )


        # المعلومات الناقصة

        if (
            not restaurant_name
            or not guests
            or not date
            or not time
        ):

            missing = []

            if not restaurant_name:
                missing.append(
                    "اسم المطعم"
                )

            if not guests:
                missing.append(
                    "عدد الأشخاص"
                )

            if not date:
                missing.append(
                    "التاريخ"
                )

            if not time:
                missing.append(
                    "الوقت"
                )


            send_whatsapp(

                from_number,

                "أحتاج هذه المعلومات لإجراء الحجز: "
                + "، ".join(missing)
            )

            return


        # البحث عن المطعم

        restaurant = restaurants.get(
            restaurant_name.lower(),
            {}
        )


        restaurant_phone = normalize_phone(

            restaurant.get("phone")
            or DEFAULT_RESTAURANT_PHONE
        )


        booking_id = str(
            len(bookings) + 1
        )


        booking = Booking(

            booking_id,

            from_number,

            customer_name,

            restaurant_name,

            restaurant_phone,

            int(guests),

            date,

            time,

            notes
        )


        bookings[booking_id] = booking


        # رسالة تأكيد للعميل

        send_whatsapp(

            from_number,

            f"تمام. سأحاول حجز "
            f"{restaurant_name} "
            f"لـ {guests} أشخاص "
            f"يوم {date} "
            f"الساعة {time}. "
            f"سأتواصل مع المطعم بالإنجليزية "
            f"وأخبرك بالنتيجة."
        )


        # الاتصال بالمطعم

        start_restaurant_call(
            booking
        )


    except Exception as exc:

        print(
            "process_whatsapp error:",
            repr(exc)
        )

        try:

            send_whatsapp(

                from_number,

                "حدث خطأ أثناء تجهيز الحجز. "
                "أرسل الطلب مرة أخرى من فضلك."
            )

        except Exception:

            pass


# =========================
# الصفحة الرئيسية
# =========================

@app.get("/")
def home():

    return {

        "service":
            "Harry AI Restaurant Booking Agent",

        "status":
            "ok",

        "whatsapp_webhook":
            "POST /whatsapp"
    }


# =========================
# Health Check
# =========================

@app.get("/health")
def health():

    return {
        "status": "ok"
    }


# =========================
# استقبال واتساب
# =========================

@app.post("/whatsapp")
async def whatsapp_webhook(

    request: Request,

    background_tasks:
        BackgroundTasks
):

    form = await request.form()

    message = str(
        form.get("Body", "")
    ).strip()

    from_number = str(
        form.get("From", "")
    ).strip()


    response = MessagingResponse()


    if (
        not message
        or not from_number
    ):

        return Response(

            content=str(response),

            media_type=
                "application/xml"
        )


    background_tasks.add_task(

        process_whatsapp,

        message,

        from_number
    )


    if AI_DISCLOSURE:

        response.message(

            "وصلتني رسالتك. "
            "أنا هاري، مساعد حجز يعمل "
            "بالذكاء الاصطناعي، "
            "وسأبدأ تجهيز الحجز الآن."
        )

    else:

        response.message(
            "وصلتني رسالتك. "
            "سأبدأ تجهيز الحجز الآن."
        )


    return Response(

        content=str(response),

        media_type=
            "application/xml"
    )


# =========================
# بداية مكالمة المطعم
# =========================

@app.post("/voice/start")
async def voice_start(
    request: Request
):

    booking_id = (
        request.query_params
        .get("booking_id")
    )

    booking = bookings.get(
        booking_id
    )


    response = VoiceResponse()


    if not booking:

        response.say(

            "Sorry, I cannot find "
            "the booking. Goodbye.",

            voice="Polly.Amy",

            language="en-GB"
        )

        response.hangup()

        return Response(

            content=str(response),

            media_type=
                "application/xml"
        )


    call_sessions[
        booking_id
    ] = {
        "turns": 0
    }


    gather = Gather(

        input="speech",

        action=(
            f"{PUBLIC_BASE_URL}"
            f"/voice/turn"
            f"?booking_id={booking_id}"
        ),

        method="POST",

        language="en-GB",

        speech_timeout="auto",

        timeout=6
    )


    opening = (

        "Hello. My name is Harry, "
        "an AI booking assistant. "

        f"I'm calling on behalf of "
        f"{booking.name}. "

        f"I'd like to book a table "
        f"for {booking.guests} people "

        f"on {booking.date} "
        f"at {booking.time}. "

        "Could you please tell me "
        "if that is available?"
    )


    gather.say(

        opening,

        voice="Polly.Amy",

        language="en-GB"
    )


    response.append(
        gather
    )


    return Response(

        content=str(response),

        media_type=
            "application/xml"
    )


# =========================
# دور المحادثة في المكالمة
# =========================

@app.post("/voice/turn")
async def voice_turn(
    request: Request
):

    form = await request.form()


    booking_id = (
        request.query_params
        .get("booking_id")
    )


    booking = bookings.get(
        booking_id
    )


    response = VoiceResponse()


    if not booking:

        response.say(

            "Sorry, goodbye.",

            voice="Polly.Amy",

            language="en-GB"
        )

        response.hangup()

        return Response(

            content=str(response),

            media_type=
                "application/xml"
        )


    restaurant_said = str(

        form.get(
            "SpeechResult",
            ""
        )

    ).strip()


    session = call_sessions.setdefault(

        booking_id,

        {
            "turns": 0
        }
    )


    session["turns"] += 1


    # لا يوجد رد

    if not restaurant_said:

        if session["turns"] >= 3:

            booking.status = (
                "no_response"
            )

            response.say(

                "I'm sorry, "
                "I cannot hear you. "
                "Thank you and goodbye.",

                voice="Polly.Amy",

                language="en-GB"
            )

            response.hangup()

        else:

            gather = Gather(

                input="speech",

                action=(
                    f"{PUBLIC_BASE_URL}"
                    f"/voice/turn"
                    f"?booking_id={booking_id}"
                ),

                method="POST",

                language="en-GB",

                speech_timeout="auto",

                timeout=6
            )


            gather.say(

                "Sorry, "
                "could you please "
                "repeat that?",

                voice="Polly.Amy",

                language="en-GB"
            )


            response.append(
                gather
            )


        return Response(

            content=str(response),

            media_type=
                "application/xml"
        )


    # =========================
    # تأكيد الحجز
    # =========================

    if detect_confirmation(
        restaurant_said
    ):

        booking.status = (
            "confirmed"
        )


        send_whatsapp(

            booking.user_whatsapp,

            f"✅ تم تأكيد الحجز في "
            f"{booking.restaurant} "
            f"لـ {booking.guests}
