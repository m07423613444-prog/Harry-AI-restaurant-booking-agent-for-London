from fastapi import FastAPI
from pydantic import BaseModel
import re

app = FastAPI(title="Harry AI - Restaurant Booking Agent")


class Booking(BaseModel):
    name: str
    phone: str
    restaurant: str
    date: str
    time: str
    guests: int


bookings = []


def valid_phone(phone: str) -> bool:
    phone = phone.strip()
    pattern = r"^\+?[0-9\s\-()]{8,20}$"
    return bool(re.match(pattern, phone))


@app.get("/")
def home():
    return {
        "message": "Harry AI Restaurant Booking Agent is running",
        "status": "online"
    }


@app.get("/restaurants")
def restaurants():
    return {
        "restaurants": [
            {
                "name": "The Ivy",
                "city": "London"
            },
            {
                "name": "Dishoom",
                "city": "London"
            },
            {
                "name": "Flat Iron",
                "city": "London"
            },
            {
                "name": "Gordon Ramsay Restaurant",
                "city": "London"
            }
        ]
    }


@app.post("/book")
def book_restaurant(booking: Booking):

    if not valid_phone(booking.phone):
        return {
            "success": False,
            "message": "رقم الهاتف غير صحيح"
        }

    if booking.guests < 1 or booking.guests > 20:
        return {
            "success": False,
            "message": "عدد الأشخاص يجب أن يكون بين 1 و20"
        }

    booking_data = booking.model_dump()
    booking_data["id"] = len(bookings) + 1

    bookings.append(booking_data)

    return {
        "success": True,
        "message": "تم تسجيل طلب الحجز بنجاح",
        "booking": booking_data
    }


@app.get("/bookings")
def get_bookings():
    return {
        "count": len(bookings),
        "
