from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime

app = FastAPI(title="Harry AI Restaurant Booking Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# تخزين الحجوزات مؤقتًا
bookings = []


class Booking(BaseModel):
    name: str
    phone: str
    restaurant: str
    date: str
    time: str
    guests: int


@app.get("/")
def home():
    return {
        "message": "Harry AI Restaurant Booking Agent is running"
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/book")
def create_booking(booking: Booking):
    new_booking = {
        "id": len(bookings) + 1,
        "name": booking.name,
        "phone": booking.phone,
        "restaurant": booking.restaurant,
        "date": booking.date,
        "time": booking.time,
        "guests": booking.guests,
        "created_at": datetime.now().isoformat(),
        "status": "pending",
    }

    bookings.append(new_booking)

    return {
        "success": True,
        "message": "تم استلام طلب الحجز بنجاح",
        "booking": new_booking,
    }


@app.get("/bookings")
def get_bookings():
    return {
        "success": True,
        "bookings": bookings
    }
