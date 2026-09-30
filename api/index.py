import os
import base64
import hashlib
import hmac
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from supabase import Client, create_client

# ============================================================
# Configuration
# ============================================================

load_dotenv(override=True)

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "").strip() or None
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "").strip() or None
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "").strip() or None

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip() or None
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip() or None

WORKNEST_LATITUDE = os.getenv("WORKNEST_LATITUDE", "33.6684509").strip()
WORKNEST_LONGITUDE = os.getenv("WORKNEST_LONGITUDE", "73.0737427").strip()
WORKNEST_ADDRESS = os.getenv(
    "WORKNEST_ADDRESS", "3rd Floor, EOBI Mall, I-8 Markaz, Islamabad"
).strip()
WORKNEST_LOCATION_NAME = os.getenv(
    "WORKNEST_LOCATION_NAME", "WorkNest Co-Working Space"
).strip()

app = FastAPI(title="WorkNest WhatsApp Chatbot")

DASHBOARD_SESSION_SECRET = os.getenv("DASHBOARD_SESSION_SECRET")
if not DASHBOARD_SESSION_SECRET:
    DASHBOARD_SESSION_SECRET = secrets.token_urlsafe(32)


# ============================================================
# Environment Validation
# ============================================================

required_env = {
    "WHATSAPP_TOKEN": WHATSAPP_TOKEN,
    "PHONE_NUMBER_ID": PHONE_NUMBER_ID,
    "VERIFY_TOKEN": VERIFY_TOKEN,
    "SUPABASE_URL": SUPABASE_URL,
    "SUPABASE_SERVICE_ROLE_KEY": SUPABASE_SERVICE_ROLE_KEY,
}

missing_env = [key for key, value in required_env.items() if not value]

if missing_env:
    print("Warning: Missing environment variables: " + ", ".join(missing_env))


# ============================================================
# Supabase
# ============================================================

supabase: Optional[Client] = None

if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


# ============================================================
# In-memory chatbot state
# ============================================================

user_states = {}


# ============================================================
# Utility Functions
# ============================================================


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def generate_booking_id():
    return f"WN-{uuid.uuid4().hex[:8].upper()}"


def generate_complaint_id():
    return f"CMP-{uuid.uuid4().hex[:6].upper()}"


def auto_close_time():
    return (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()


def make_password_hash(password, salt=None):
    salt_bytes = bytes.fromhex(salt) if salt else secrets.token_bytes(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_bytes,
        310000,
    )
    return salt_bytes.hex(), password_hash.hex()


def make_dashboard_session(user_name):
    expires_at = int(datetime.now(timezone.utc).timestamp()) + 8 * 60 * 60
    payload = f"{user_name}|{expires_at}"
    signature = hmac.new(
        DASHBOARD_SESSION_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return (
        base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
        + "."
        + base64.urlsafe_b64encode(signature).decode("ascii")
    )


def get_dashboard_session_user_name(token):
    if not token or "." not in token:
        return None

    encoded_payload, encoded_signature = token.split(".", 1)

    try:
        payload = base64.urlsafe_b64decode(encoded_payload).decode("utf-8")
        signature = base64.urlsafe_b64decode(encoded_signature)
        expected_signature = hmac.new(
            DASHBOARD_SESSION_SECRET.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).digest()

        if not hmac.compare_digest(signature, expected_signature):
            return None

        user_name, expires_at = payload.rsplit("|", 1)

        if int(expires_at) <= int(datetime.now(timezone.utc).timestamp()):
            return None

        return user_name

    except (ValueError, TypeError, UnicodeDecodeError):
        return None


# ============================================================
# Main Menu
# ============================================================


def main_menu():

    return (
        "Welcome to WorkNest Co-Working!\n\n"
        "How can we help you today?\n\n"
        "1. Book a Workspace\n"
        "2. My Booking\n"
        "3. Pricing & Plans\n"
        "4. Facilities\n"
        "5. Complaints / Support\n"
        "6. Talk to Reception\n"
        "7. Location & Directions\n"
        "0. Main Menu\n\n"
        "Reply with a number."
    )


# ============================================================
# Supabase Contact
# ============================================================


def get_or_create_contact(phone_number):

    if not supabase:
        return None

    try:

        result = (
            supabase.table("whatsapp_contacts")
            .select("*")
            .eq("phone_number", phone_number)
            .limit(1)
            .execute()
        )

        if result.data:

            contact = result.data[0]

            supabase.table("whatsapp_contacts").update({"updated_at": utc_now()}).eq(
                "id", contact["id"]
            ).execute()

            return contact

        result = (
            supabase.table("whatsapp_contacts")
            .insert(
                {
                    "phone_number": phone_number,
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                }
            )
            .execute()
        )

        if result.data:
            return result.data[0]

    except Exception as e:

        print("Supabase contact error:", e)

    return None


# ============================================================
# Supabase Conversation
# ============================================================


def get_or_create_conversation(contact_id):

    if not supabase or not contact_id:
        return None

    try:

        result = (
            supabase.table("whatsapp_conversations")
            .select("*")
            .eq("contact_id", contact_id)
            .eq("status", "open")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if result.data:
            return result.data[0]

        result = (
            supabase.table("whatsapp_conversations")
            .insert(
                {
                    "contact_id": contact_id,
                    "status": "open",
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                }
            )
            .execute()
        )

        if result.data:
            return result.data[0]

    except Exception as e:

        print("Supabase conversation error:", e)

    return None


# ============================================================
# Save Message
# ============================================================


def save_message(
    conversation_id,
    direction,
    message_text,
    message_type="text",
    whatsapp_message_id=None,
):

    if not supabase or not conversation_id:
        return None

    try:

        result = (
            supabase.table("whatsapp_messages")
            .insert(
                {
                    "conversation_id": conversation_id,
                    "whatsapp_message_id": whatsapp_message_id,
                    "direction": direction,
                    "message_type": message_type,
                    "message_text": message_text,
                    "created_at": utc_now(),
                }
            )
            .execute()
        )

        supabase.table("whatsapp_conversations").update({"updated_at": utc_now()}).eq(
            "id", conversation_id
        ).execute()

        if result.data:
            return result.data[0]

    except Exception as e:

        print("Supabase message error:", e)

    return None


# ============================================================
# Create Complaint
# ============================================================


def create_complaint(
    complaint_id,
    complaint,
    category,
    contact_id=None,
    conversation_id=None,
    is_followup=False,
    previous_complaint_id=None,
):

    if not supabase:
        return None

    try:

        result = (
            supabase.table("complaints")
            .insert(
                {
                    "complaint_id": complaint_id,
                    "contact_id": contact_id,
                    "conversation_id": conversation_id,
                    "complaint": complaint,
                    "category": category,
                    "status": "open",
                    "is_followup": is_followup,
                    "previous_complaint_id": previous_complaint_id,
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                }
            )
            .execute()
        )

        if result.data:
            return result.data[0]

    except Exception as e:

        print("Complaint creation error:", e)

    return None


# ============================================================
# Create Booking
# ============================================================


def create_booking(
    booking_id,
    workspace_type,
    customer_name=None,
    customer_phone=None,
    seats=None,
    contact_id=None,
    conversation_id=None,
    status="pending",
):
    if not supabase:
        return None

    try:
        result = (
            supabase.table("bookings")
            .insert(
                {
                    "booking_id": booking_id,
                    "contact_id": contact_id,
                    "conversation_id": conversation_id,
                    "workspace_type": workspace_type,
                    "customer_name": customer_name,
                    "customer_phone": customer_phone,
                    "seats": str(seats) if seats else None,
                    "status": status,
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                }
            )
            .execute()
        )

        if result.data:
            return result.data[0]

    except Exception as e:
        print("Booking creation error:", e)

    return None


# ============================================================

# WhatsApp Send Message
# ============================================================


def send_whatsapp_message(to, message):

    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:

        print("WhatsApp credentials are missing.")

        return False

    url = f"https://graph.facebook.com/v23.0/" f"{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": (f"Bearer {WHATSAPP_TOKEN}"),
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message},
    }

    try:

        response = requests.post(url, headers=headers, json=payload, timeout=20)

        print("WhatsApp API status:", response.status_code)

        print("WhatsApp API response:", response.text)

        return response.ok

    except Exception as e:

        print("WhatsApp send error:", e)

        return False


def send_whatsapp_text(phone_number, message):

    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        raise RuntimeError("WhatsApp credentials are missing.")

    url = f"https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": phone_number,
        "type": "text",
        "text": {"body": message},
    }

    return requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=15,
    )


def send_whatsapp_template(
    phone_number,
    template_name="worknest_agent_message",
    language_code="en_US",
    body_parameters=None,
):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        raise RuntimeError("WhatsApp credentials are missing.")

    url = f"https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    params = [{"type": "text", "text": str(p)} for p in (body_parameters or [])]

    payload = {
        "messaging_product": "whatsapp",
        "to": phone_number,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {
                "code": language_code,
            },
            "components": [
                {
                    "type": "body",
                    "parameters": params,
                }
            ],
        },
    }

    response = requests.post(url, headers=headers, json=payload, timeout=20)
    if not response.ok:
        # Retry with alternate English language codes if en_US fails
        for alt_lang in ["en", "en_GB", "en_US"]:
            if alt_lang != language_code:
                payload["template"]["language"]["code"] = alt_lang
                alt_res = requests.post(url, headers=headers, json=payload, timeout=20)
                if alt_res.ok:
                    return alt_res
    return response


def send_whatsapp_location(
    to,
    latitude=None,
    longitude=None,
    name=None,
    address=None,
):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        print("WhatsApp credentials missing for location pin.")
        return False

    url = f"https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "location",
        "location": {
            "latitude": str(latitude or WORKNEST_LATITUDE),
            "longitude": str(longitude or WORKNEST_LONGITUDE),
            "name": str(name or WORKNEST_LOCATION_NAME),
            "address": str(address or WORKNEST_ADDRESS),
        },
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        return response.ok
    except Exception as e:
        print("Location send error:", e)
        return False


def send_resolved_notification(phone_number, complaint_id):

    message = (
        "WorkNest Complaint Update\n\n"
        f"We are pleased to confirm that your complaint {complaint_id} has been resolved.\n\n"
        "To help us verify this, please let us know whether the issue is still occurring.\n\n"
        "1. Yes, the issue persists\n"
        "2. No, the issue is resolved\n\n"
        "Please reply with 1 or 2."
    )

    return send_whatsapp_text(phone_number, message)


# ============================================================
# Chatbot
# ============================================================


def chatbot(user_id, message, contact_id=None, conversation_id=None):

    message = message.strip()
    lower_message = message.lower()

    # --------------------------------------------------------
    # Initialize user state
    # --------------------------------------------------------

    if user_id not in user_states:

        user_states[user_id] = {"state": "main_menu", "booking": {}, "complaint": {}}

    state_data = user_states[user_id]
    state = state_data["state"]

    # --------------------------------------------------------
    # Global Menu Commands
    # --------------------------------------------------------

    if lower_message in ["menu", "home", "start", "0"]:

        state_data["state"] = "main_menu"
        state_data["booking"] = {}
        state_data["complaint"] = {}

        return main_menu()

    if lower_message in [
        "location",
        "directions",
        "address",
        "map",
        "pin",
        "where are you",
    ] or "where is worknest" in lower_message or "how to reach" in lower_message:

        state_data["state"] = "main_menu"
        state_data["booking"] = {}
        state_data["complaint"] = {}

        send_whatsapp_location(user_id)

        return (
            "WorkNest Location & Directions\n\n"
            f"Address: {WORKNEST_ADDRESS}\n"
            "Hours: Open 24/7 for registered members. Front reception: 9:00 AM - 9:00 PM.\n\n"
            "An interactive WhatsApp map pin has been sent directly below. Tap the pin to navigate with Google Maps or Apple Maps.\n\n"
            "Type 0 to return to the main menu."
        )

    # --------------------------------------------------------
    # Resolved complaint response
    # --------------------------------------------------------

    if state == "main_menu" and message in ["1", "2"] and supabase and contact_id:

        resolved_result = (
            supabase.table("complaints")
            .select("*")
            .eq("contact_id", contact_id)
            .eq("status", "resolved")
            .eq("resolved_notification_sent", True)
            .is_("resolution_response", "null")
            .order("resolved_notification_sent_at", desc=True)
            .limit(1)
            .execute()
        )

        if resolved_result.data:

            resolved_complaint = resolved_result.data[0]
            resolved_complaint_id = resolved_complaint["complaint_id"]

            if message == "2":

                supabase.table("complaints").update(
                    {
                        "status": "closed",
                        "resolution_response": "resolved",
                        "resolution_response_at": utc_now(),
                        "auto_close_at": None,
                        "updated_at": utc_now(),
                    }
                ).eq("complaint_id", resolved_complaint_id).execute()

                user_states[user_id] = {
                    "state": "main_menu",
                    "booking": {},
                    "complaint": {},
                }

                return (
                    "Thank you for confirming.\n\n"
                    f"Complaint {resolved_complaint_id} has been closed.\n\n"
                    "If you need anything else, please type 0."
                )

            followup_complaint_id = generate_complaint_id()
            followup_complaint = create_complaint(
                complaint_id=followup_complaint_id,
                complaint=resolved_complaint.get("complaint", ""),
                category=resolved_complaint.get("category") or "Other",
                contact_id=contact_id,
                conversation_id=conversation_id,
                is_followup=True,
                previous_complaint_id=resolved_complaint_id,
            )

            if not followup_complaint:
                return (
                    "We're sorry the issue is still persisting.\n\n"
                    "We were unable to create a follow-up complaint right "
                    "now. Please try again."
                )

            supabase.table("complaints").update(
                {
                    "resolution_response": "persists",
                    "resolution_response_at": utc_now(),
                    "auto_close_at": None,
                    "updated_at": utc_now(),
                }
            ).eq("complaint_id", resolved_complaint_id).execute()

            user_states[user_id] = {
                "state": "main_menu",
                "booking": {},
                "complaint": {},
            }

            return (
                "We're sorry the issue is still persisting.\n\n"
                "A new complaint has been submitted for further review.\n\n"
                f"Previous Complaint ID: {resolved_complaint_id}\n"
                f"New Complaint ID: {followup_complaint_id}\n\n"
                f"Category: {resolved_complaint.get('category') or 'Other'}\n"
                "Status: Open\n\n"
                "Our team will investigate the issue again and contact you "
                "if further information is required.\n\n"
                "Type 0 to return to the main menu."
            )

    # ========================================================
    # MAIN MENU
    # ========================================================

    if state == "main_menu":

        if lower_message in {
            "hi",
            "hello",
            "hey",
            "salam",
            "assalamualaikum",
        }:
            return "Hello!.\n\n" + main_menu()

        # ----------------------------------------------------
        # Book Workspace
        # ----------------------------------------------------

        if message == "1":

            state_data["state"] = "booking_type"

            return (
                "Book a Workspace\n\n"
                "Please select a workspace type:\n\n"
                "1. Shared Seat\n"
                "2. Private Office\n"
                "3. Meeting Room\n"
                "4. Conference Room\n\n"
                "Type 0 for the main menu.\n\n"
                "Reply with a number."
            )

        # ----------------------------------------------------
        # My Booking
        # ----------------------------------------------------

        if message == "2":

            state_data["state"] = "booking_lookup"

            return (
                "My Booking\n\n"
                "Please enter your Booking ID.\n\n"
                "Example: WN-ABC12345"
            )

        # ----------------------------------------------------
        # Pricing
        # ----------------------------------------------------

        if message == "3":

            state_data["state"] = "pricing"

            return (
                "Pricing & Plans\n\n"
                "1. Private Office - Starting from PKR 35,000 per seat\n"
                "2. Shared Space - Starting from PKR 25,000 per seat\n"
                "3. Meeting / Conference Room - Starting from PKR 6,000 per hour\n\n"
                "Type 0 for the main menu.\n\n"
                "Reply with a number."
            )

        # ----------------------------------------------------
        # Facilities
        # ----------------------------------------------------

        if message == "4":

            state_data["state"] = "facilities"

            return (
                "WorkNest Facilities\n\n"
                "1. High-Speed WiFi\n"
                "2. Power Backup\n"
                "3. Meeting Rooms\n"
                "4. Private Offices\n"
                "5. Dedicated Desks\n"
                "6. Printing & Scanning\n"
                "7. Kitchen & Refreshments\n"
                "8. Parking\n"
                "9. Reception Support\n\n"
                "Type 0 to return to the main menu."
            )

        # ----------------------------------------------------
        # Complaints
        # ----------------------------------------------------

        if message == "5":

            state_data["state"] = "complaint_category"

            return (
                "Complaints / Support\n\n"
                "Please select a category:\n\n"
                "1. Internet / WiFi\n"
                "2. Electricity / AC\n"
                "3. Cleaning\n"
                "4. Noise\n"
                "5. Access / Entry\n"
                "6. Booking Issue\n"
                "7. Other\n\n"
                "Type 0 for the main menu.\n\n"
                "Reply with a number."
            )

        # ----------------------------------------------------
        # Reception
        # ----------------------------------------------------

        if message == "6":

            state_data["state"] = "reception"

            return (
                "Talk to Reception\n\n"
                "Reception: +92 XXX XXXXXXX\n"
                "Hours: 9:00 AM - 9:00 PM\n\n"
                "Type 0 to return to the main menu."
            )

        # ----------------------------------------------------
        # Location & Directions
        # ----------------------------------------------------

        if message == "7" or lower_message in [
            "location",
            "directions",
            "address",
            "map",
            "pin",
        ]:

            state_data["state"] = "main_menu"

            send_whatsapp_location(user_id)

            return (
                "WorkNest Location & Directions\n\n"
                f"Address: {WORKNEST_ADDRESS}\n"
                "Hours: Open 24/7 for registered members. Front reception: 9:00 AM - 9:00 PM.\n\n"
                "An interactive WhatsApp map pin has been sent directly below. Tap the pin to navigate with Google Maps or Apple Maps.\n\n"
                "Type 0 to return to the main menu."
            )

        return "Please select a valid option.\n\n" + main_menu()

    # ========================================================
    # BOOKING TYPE
    # ========================================================

    if state == "booking_type":

        workspace_types = {
            "1": "Shared Seat",
            "2": "Private Office",
            "3": "Meeting Room",
            "4": "Conference Room",
        }

        if message not in workspace_types:

            return (
                "Please select a valid workspace type:\n\n"
                "1. Shared Seat\n"
                "2. Private Office\n"
                "3. Meeting Room\n"
                "4. Conference Room\n\n"
                "Type 0 for the main menu."
            )

        state_data["booking"]["workspace_type"] = workspace_types[message]

        state_data["state"] = "booking_name"

        return (
            f"Selected: {workspace_types[message]}\n\n"
            "Please enter your Full Name.\n\n"
            "Example: Luqman Ahmad\n\n"
            "Type 0 for the main menu."
        )

    # ========================================================
    # BOOKING NAME
    # ========================================================

    if state == "booking_name":

        if len(message) < 2:
            return "Please enter a valid full name.\n\nExample: Luqman Ahmad\n\nType 0 for the main menu."

        state_data["booking"]["name"] = message

        state_data["state"] = "booking_phone"

        return (
            f"Thank you, {message}!\n\n"
            "Please enter your Contact Phone Number.\n\n"
            "Example: 03001234567\n\n"
            "Type 0 for the main menu."
        )

    # ========================================================
    # BOOKING PHONE
    # ========================================================

    if state == "booking_phone":

        cleaned_phone = re.sub(r"[^\d+]", "", message)
        if len(cleaned_phone) < 7:
            return (
                "Please enter a valid contact phone number.\n\n"
                "Example: 03001234567\n\n"
                "Type 0 for the main menu."
            )

        state_data["booking"]["phone"] = message

        state_data["state"] = "booking_seats"

        return (
            "Please enter the number of seats / persons required.\n\n"
            "Example: 1 (or 5)\n\n"
            "Type 0 for the main menu."
        )

    # ========================================================
    # BOOKING SEATS
    # ========================================================

    if state == "booking_seats":

        state_data["booking"]["seats"] = message

        state_data["state"] = "booking_confirmation"

        booking = state_data["booking"]

        return (
            "Please confirm your booking details:\n\n"
            f"Workspace: {booking.get('workspace_type', '')}\n"
            f"Name: {booking.get('name', '')}\n"
            f"Phone: {booking.get('phone', '')}\n"
            f"Seats / Persons: {booking.get('seats', '')}\n\n"
            "Reply with:\n"
            "1. Confirm\n"
            "2. Cancel\n\n"
            "Type 0 for the main menu."
        )

    # ========================================================
    # BOOKING CONFIRMATION
    # ========================================================

    if state == "booking_confirmation":

        if message == "1":

            booking_id = generate_booking_id()

            state_data["booking"]["booking_id"] = booking_id

            booking = state_data["booking"]

            state_data["state"] = "main_menu"

            create_booking(
                booking_id=booking_id,
                workspace_type=booking.get("workspace_type", ""),
                customer_name=booking.get("name", ""),
                customer_phone=booking.get("phone", ""),
                seats=booking.get("seats", ""),
                contact_id=contact_id,
                conversation_id=conversation_id,
                status="pending",
            )

            if contact_id and supabase and booking.get("name"):
                try:
                    supabase.table("whatsapp_contacts").update({
                        "name": booking["name"],
                        "updated_at": utc_now(),
                    }).eq("id", contact_id).execute()
                except Exception as e:
                    print("Failed to update contact name from booking:", e)

            return (
                "Booking request received!\n\n"
                f"Booking ID: {booking_id}\n\n"
                f"Workspace: {booking.get('workspace_type', '')}\n"
                f"Name: {booking.get('name', '')}\n"
                f"Phone: {booking.get('phone', '')}\n"
                f"Seats / Persons: {booking.get('seats', '')}\n\n"
                "Our reception team will contact you shortly to confirm availability and finalize your booking.\n\n"
                "Thank you for choosing WorkNest!\n\n"
                "Type 0 to return to the main menu."
            )

        if message == "2":

            state_data["state"] = "main_menu"
            state_data["booking"] = {}

            return "Your booking request has been cancelled.\n\n" + main_menu()

        return (
            "Please reply with:\n\n"
            "1. Confirm\n"
            "2. Cancel\n\n"
            "Type 0 for the main menu."
        )

    # ========================================================
    # BOOKING LOOKUP
    # ========================================================

    if state == "booking_lookup":

        booking_id = message.upper().strip()

        state_data["state"] = "main_menu"

        booking_details = None
        if supabase:
            try:
                res = (
                    supabase.table("bookings")
                    .select("*")
                    .eq("booking_id", booking_id)
                    .limit(1)
                    .execute()
                )
                if res.data:
                    booking_details = res.data[0]
            except Exception as e:
                print("Error looking up booking:", e)

        if booking_details:
            return (
                "WorkNest Booking Found!\n\n"
                f"Booking ID: {booking_details.get('booking_id')}\n"
                f"Workspace: {booking_details.get('workspace_type', 'N/A')}\n"
                f"Name: {booking_details.get('customer_name') or 'N/A'}\n"
                f"Phone: {booking_details.get('customer_phone') or 'N/A'}\n"
                f"Seats: {booking_details.get('seats') or 'N/A'}\n"
                f"Status: {booking_details.get('status', 'pending').capitalize()}\n\n"
                "Type 0 to return to the main menu."
            )

        return (
            f"Booking ID: {booking_id}\n\n"
            "No booking was found with this ID.\n"
            "Please check your ID or contact reception.\n\n"
            "Type 0 to return to the main menu."
        )


    # ========================================================
    # PRICING
    # ========================================================

    if state == "pricing":

        pricing = {
            "1": "Private Office - Starting from PKR 35,000 per seat",
            "2": "Shared Space - Starting from PKR 25,000 per seat",
            "3": "Meeting / Conference Room - Starting from PKR 6,000 per hour",
        }

        if message in pricing:

            state_data["state"] = "main_menu"

            return (
                f"{pricing[message]}\n\n"
                "For exact pricing and availability, "
                "please contact reception.\n\n"
                "Type 0 to return to the main menu."
            )

        return (
            "Please select a valid pricing option:\n\n"
            "1. Private Office\n"
            "2. Shared Space\n"
            "3. Meeting / Conference Room\n\n"
            "Type 0 for the main menu."
        )

    # ========================================================
    # FACILITIES
    # ========================================================

    if state == "facilities":

        state_data["state"] = "main_menu"

        return (
            "WorkNest provides:\n\n"
            "High-Speed WiFi\n"
            "Power Backup\n"
            "Meeting Rooms\n"
            "Private Offices\n"
            "Dedicated Desks\n"
            "Printing & Scanning\n"
            "Kitchen & Refreshments\n"
            "Parking\n"
            "Reception Support\n\n"
            "Type 0 to return to the main menu."
        )

    # ========================================================
    # COMPLAINT CATEGORY
    # ========================================================

    if state == "complaint_category":

        categories = {
            "1": "Internet / WiFi",
            "2": "Electricity / AC",
            "3": "Cleaning",
            "4": "Noise",
            "5": "Access / Entry",
            "6": "Booking Issue",
            "7": "Other",
        }

        if message not in categories:

            return (
                "Please select a valid category:\n\n"
                "1. Internet / WiFi\n"
                "2. Electricity / AC\n"
                "3. Cleaning\n"
                "4. Noise\n"
                "5. Access / Entry\n"
                "6. Booking Issue\n"
                "7. Other\n\n"
                "Type 0 for the main menu."
            )

        followup_complaint_id = state_data["complaint"].get("followup_complaint_id")
        state_data["complaint"] = {
            "category": categories[message],
            "followup_complaint_id": followup_complaint_id,
        }
        state_data["state"] = "complaint_description"

        return f"Category: {categories[message]}\n\n" "Please describe your issue."

    # ========================================================
    # COMPLAINT DESCRIPTION
    # ========================================================

    if state == "complaint_description":

        complaint_id = generate_complaint_id()

        complaint_text = message

        category = state_data["complaint"]["category"]

        # Store complaint in Supabase
        complaint = create_complaint(
            complaint_id=complaint_id,
            complaint=complaint_text,
            category=category,
            contact_id=contact_id,
            conversation_id=conversation_id,
        )

        # Do not tell user it was submitted if
        # database insertion failed.
        if not complaint:

            return (
                "We were unable to submit your complaint "
                "right now.\n\n"
                "Please try again."
            )

        state_data["complaint"]["description"] = complaint_text

        state_data["complaint"]["complaint_id"] = complaint_id

        followup_complaint_id = state_data["complaint"].get("followup_complaint_id")

        if followup_complaint_id:
            response_text = (
                "Your new complaint has been submitted.\n\n"
                f"Previous Complaint ID: {followup_complaint_id}\n"
                f"New Complaint ID: {complaint_id}\n\n"
                f"Category: {category}\n"
                "Status: Open\n\n"
                "Our team will review the issue again and contact you "
                "if further information is required.\n\n"
                "Type 0 to return to the main menu."
            )
        else:
            response_text = (
                "Your complaint has been submitted.\n\n"
                f"Complaint ID: {complaint_id}\n"
                f"Category: {category}\n"
                "Status: Open\n\n"
                "Our team will review your complaint and contact you "
                "if further information is required.\n\n"
                "Type 0 to return to the main menu."
            )

        user_states[user_id] = {
            "state": "main_menu",
            "booking": {},
            "complaint": {},
        }

        return response_text

    # ========================================================
    # RECEPTION
    # ========================================================

    if state == "reception":

        return (
            "Reception\n\n"
            "Phone: +92 XXX XXXXXXX\n"
            "Hours: 9:00 AM - 9:00 PM\n\n"
            "Type 0 to return to the main menu."
        )

    # ========================================================
    # Fallback
    # ========================================================

    state_data["state"] = "main_menu"

    return main_menu()


# ============================================================
# Root
# ============================================================


@app.get("/")
async def root():

    return {"status": "online", "service": "WorkNest WhatsApp Chatbot"}


@app.api_route("/privacy", methods=["GET", "HEAD"], response_class=HTMLResponse)
@app.api_route("/data-deletion", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def privacy_policy():
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Privacy Policy & Data Deletion - WorkNest Bot</title>
    <style>
        :root {
            --primary: #10b981;
            --primary-dark: #059669;
            --bg: #0f172a;
            --card-bg: #1e293b;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --border: #334155;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg);
            color: var(--text-main);
            line-height: 1.7;
            padding: 40px 20px;
        }
        .container {
            max-width: 820px;
            margin: 0 auto;
            background: var(--card-bg);
            padding: 40px;
            border-radius: 16px;
            border: 1px solid var(--border);
            box-shadow: 0 10px 25px rgba(0,0,0,0.3);
        }
        .badge {
            display: inline-block;
            background: rgba(16, 185, 129, 0.15);
            color: var(--primary);
            padding: 4px 12px;
            border-radius: 9999px;
            font-size: 0.85rem;
            font-weight: 600;
            margin-bottom: 12px;
        }
        h1 {
            font-size: 2rem;
            color: #ffffff;
            margin-bottom: 8px;
        }
        .updated {
            color: var(--text-muted);
            font-size: 0.9rem;
            margin-bottom: 30px;
            border-bottom: 1px solid var(--border);
            padding-bottom: 16px;
        }
        h2 {
            font-size: 1.25rem;
            color: #ffffff;
            margin-top: 28px;
            margin-bottom: 12px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        p, li {
            color: var(--text-muted);
            font-size: 0.975rem;
            margin-bottom: 12px;
        }
        ul {
            padding-left: 20px;
            margin-bottom: 16px;
        }
        .highlight-box {
            background: rgba(16, 185, 129, 0.08);
            border-left: 4px solid var(--primary);
            padding: 16px 20px;
            border-radius: 0 8px 8px 0;
            margin: 20px 0;
        }
        .highlight-box p {
            color: var(--text-main);
            margin: 0;
            font-weight: 500;
        }
        footer {
            margin-top: 40px;
            border-top: 1px solid var(--border);
            padding-top: 20px;
            text-align: center;
            font-size: 0.875rem;
            color: var(--text-muted);
        }
    </style>
</head>
<body>
    <div class="container">
        <span class="badge">Official Policy</span>
        <h1>Privacy Policy &amp; Data Deletion</h1>
        <p class="updated">Effective Date: September 2026 | WorkNest Co-Working</p>

        <h2>1. Overview</h2>
        <p>WorkNest Bot provides automated assistance, desk/office bookings, customer service inquiries, and issue resolution for members and visitors of WorkNest Co-Working spaces via WhatsApp.</p>

        <h2>2. Information We Collect</h2>
        <p>When you interact with our WhatsApp bot, we collect minimal information necessary to deliver our services:</p>
        <ul>
            <li><strong>WhatsApp Phone Number:</strong> Used as your unique identifier to manage inquiries and reservations.</li>
            <li><strong>Profile Display Name:</strong> The name provided by your WhatsApp profile.</li>
            <li><strong>Message History:</strong> Transcripts of conversations sent to the bot, booking requests, and feedback/complaint reports.</li>
        </ul>

        <h2>3. How We Use Your Information</h2>
        <ul>
            <li>To process and confirm workspace and meeting room reservations.</li>
            <li>To log, track, and notify you regarding the resolution of workspace maintenance or service tickets.</li>
            <li>To deliver customer support and operational updates relevant to your membership or booking.</li>
        </ul>

        <h2>4. Data Sharing &amp; Third Parties</h2>
        <p>We do not sell, rent, or trade your personal information. Data transmitted through WhatsApp is securely processed using Meta WhatsApp Cloud API and stored in encrypted Supabase database instances. We do not share data with any external advertisers or unverified third parties.</p>

        <div class="highlight-box">
            <h2>5. User Data Deletion Instructions</h2>
            <p>You have full control over your personal data. To request the complete deletion of your phone number, profile, and conversation logs from our database:</p>
            <ul style="margin-top: 10px; margin-bottom: 0;">
                <li>Send a WhatsApp message saying <strong>"DELETE MY DATA"</strong> or <strong>"REMOVE MY ACCOUNT"</strong> to the WorkNest Bot.</li>
                <li>Or send an email with your WhatsApp phone number to <strong>support@worknest.offices</strong> requesting account data deletion.</li>
            </ul>
            <p style="margin-top: 10px;">All associated records will be permanently purged within 48 hours of verification.</p>
        </div>

        <h2>6. Contact Us</h2>
        <p>If you have any questions about this Privacy Policy or our data practices, please reach out to our team at <strong>support@worknest.offices</strong>.</p>

        <footer>
            &copy; 2026 WorkNest Co-Working. All rights reserved.
        </footer>
    </div>
</body>
</html>"""



# ============================================================
# WhatsApp Webhook Verification
# ============================================================


@app.get("/webhook")
async def verify_webhook(request: Request):

    params = request.query_params

    mode = params.get("hub.mode")

    token = params.get("hub.verify_token")

    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:

        return HTMLResponse(content=challenge or "", status_code=200)

    return JSONResponse(content={"error": "Verification failed"}, status_code=403)


# ============================================================
# WhatsApp Webhook
# ============================================================


@app.post("/webhook")
async def whatsapp_webhook(request: Request):

    try:

        data = await request.json()

        print("Incoming webhook:")

        print(data)

        entries = data.get("entry", [])

        for entry in entries:

            changes = entry.get("changes", [])

            for change in changes:

                value = change.get("value", {})

                messages = value.get("messages", [])

                contacts = value.get("contacts", [])

                if not messages:
                    continue

                for message in messages:

                    sender = message.get("from")

                    message_id = message.get("id")

                    message_type = message.get("type")

                    if not sender:
                        continue

                    # --------------------------------------------
                    # Contact name
                    # --------------------------------------------

                    contact_name = None

                    if contacts:

                        contact_data = contacts[0]

                        profile = contact_data.get("profile", {})

                        contact_name = profile.get("name")

                    # --------------------------------------------
                    # Contact
                    # --------------------------------------------

                    contact = get_or_create_contact(sender)

                    if contact and contact_name and supabase:

                        try:

                            supabase.table("whatsapp_contacts").update(
                                {"name": contact_name, "updated_at": utc_now()}
                            ).eq("id", contact["id"]).execute()

                        except Exception as e:

                            print("Contact name update error:", e)

                    # --------------------------------------------
                    # Conversation
                    # --------------------------------------------

                    conversation = None

                    if contact:

                        conversation = get_or_create_conversation(contact["id"])

                    conversation_id = None

                    if conversation:

                        conversation_id = conversation["id"]

                    # --------------------------------------------
                    # Extract message
                    # --------------------------------------------

                    if message_type == "text":

                        message_text = message.get("text", {}).get("body", "")

                    else:

                        message_text = f"[{message_type} message]"

                    # --------------------------------------------
                    # Save incoming message
                    # --------------------------------------------

                    save_message(
                        conversation_id=conversation_id,
                        direction="incoming",
                        message_text=message_text,
                        message_type=message_type,
                        whatsapp_message_id=message_id,
                    )

                    # --------------------------------------------
                    # Chatbot
                    # --------------------------------------------

                    if message_type == "text":

                        response_text = chatbot(
                            sender,
                            message_text,
                            contact_id=(contact["id"] if contact else None),
                            conversation_id=conversation_id,
                        )

                    else:

                        response_text = (
                            "Currently, I can process text "
                            "messages only.\n\n"
                            "Please send a text message.\n\n" + main_menu()
                        )

                    # --------------------------------------------
                    # Send response
                    # --------------------------------------------

                    sent = send_whatsapp_message(sender, response_text)

                    # --------------------------------------------
                    # Save outgoing message
                    # --------------------------------------------

                    if sent:

                        save_message(
                            conversation_id=conversation_id,
                            direction="outgoing",
                            message_text=response_text,
                            message_type="text",
                        )

        return {"status": "received"}

    except Exception as e:

        print("Webhook error:", e)

        return JSONResponse(
            content={"status": "error", "message": str(e)}, status_code=200
        )


# ============================================================
# Dashboard - Conversations API
# ============================================================


@app.get("/api/conversations")
async def get_conversations():

    if not supabase:

        return JSONResponse(
            content={"error": "Supabase is not configured"}, status_code=500
        )

    try:

        try:
            result = (
                supabase.table("whatsapp_conversations")
                .select(
                    "id,"
                    "contact_id,"
                    "status,"
                    "created_at,"
                    "updated_at,"
                    "whatsapp_contacts("
                    "id,"
                    "phone_number,"
                    "name,"
                    "tags,"
                    "admin_notes"
                    ")"
                )
                .order("updated_at", desc=True)
                .execute()
            )
        except Exception:
            result = (
                supabase.table("whatsapp_conversations")
                .select(
                    "id,"
                    "contact_id,"
                    "status,"
                    "created_at,"
                    "updated_at,"
                    "whatsapp_contacts("
                    "id,"
                    "phone_number,"
                    "name"
                    ")"
                )
                .order("updated_at", desc=True)
                .execute()
            )

        conversations = []

        for conversation in result.data or []:

            contact = conversation.get("whatsapp_contacts") or {}

            conversations.append(
                {
                    "id": conversation.get("id"),
                    "contact_id": conversation.get("contact_id") or contact.get("id"),
                    "phone_number": contact.get("phone_number"),
                    "name": contact.get("name"),
                    "tags": contact.get("tags") or "",
                    "admin_notes": contact.get("admin_notes") or "",
                    "status": conversation.get("status"),
                    "created_at": conversation.get("created_at"),
                    "updated_at": conversation.get("updated_at"),
                }
            )

        return conversations

    except Exception as e:

        print("Conversation API error:", e)

        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/api/dashboard/stats")
async def get_dashboard_stats():

    if not supabase:
        return JSONResponse(
            content={"error": "Supabase is not configured"}, status_code=500
        )

    try:
        # 1. Active Conversations
        conv_res = (
            supabase.table("whatsapp_conversations")
            .select("id", count="exact")
            .eq("status", "open")
            .execute()
        )
        active_conv = (
            conv_res.count if conv_res.count is not None else len(conv_res.data or [])
        )

        # 2. Pending Bookings
        bk_res = (
            supabase.table("bookings")
            .select("id", count="exact")
            .eq("status", "pending")
            .execute()
        )
        pending_bk = (
            bk_res.count if bk_res.count is not None else len(bk_res.data or [])
        )

        # 3. Confirmed This Week (last 7 days)
        seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        conf_res = (
            supabase.table("bookings")
            .select("id", count="exact")
            .eq("status", "confirmed")
            .gte("updated_at", seven_days_ago)
            .execute()
        )
        confirmed_week = (
            conf_res.count if conf_res.count is not None else len(conf_res.data or [])
        )

        # 4. Open Complaints
        comp_res = (
            supabase.table("complaints")
            .select("id", count="exact")
            .in_("status", ["open", "in_progress"])
            .execute()
        )
        open_comp = (
            comp_res.count if comp_res.count is not None else len(comp_res.data or [])
        )

        return {
            "active_conversations": active_conv,
            "pending_bookings": pending_bk,
            "confirmed_this_week": confirmed_week,
            "open_complaints": open_comp,
        }

    except Exception as e:
        print("Dashboard stats error:", e)
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.patch("/api/contacts/{contact_id}/meta")
async def update_contact_meta(contact_id: int, request: Request):

    if not supabase:
        return JSONResponse(
            content={"error": "Supabase is not configured"}, status_code=500
        )

    try:
        body = await request.json()
        update_data = {"updated_at": datetime.now(timezone.utc).isoformat()}
        if "tags" in body:
            update_data["tags"] = str(body.get("tags") or "").strip()
        if "admin_notes" in body:
            update_data["admin_notes"] = str(body.get("admin_notes") or "").strip()

        res = (
            supabase.table("whatsapp_contacts")
            .update(update_data)
            .eq("id", contact_id)
            .execute()
        )

        return {"success": True, "contact": res.data[0] if res.data else {}}

    except Exception as e:
        print("Update contact meta error:", e)
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ============================================================
# Dashboard - Messages API
# ============================================================


@app.get("/api/conversations/{conversation_id}/messages")
async def get_conversation_messages(conversation_id: int):

    if not supabase:

        return JSONResponse(
            content={"error": "Supabase is not configured"}, status_code=500
        )

    try:

        result = (
            supabase.table("whatsapp_messages")
            .select(
                "id,"
                "conversation_id,"
                "whatsapp_message_id,"
                "direction,"
                "message_type,"
                "message_text,"
                "created_at"
            )
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=False)
            .execute()
        )

        return result.data or []

    except Exception as e:

        print("Messages API error:", e)

        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/conversations/{conversation_id}/messages")
async def send_manual_message(conversation_id: int, request: Request):

    if not supabase:
        return JSONResponse(
            content={"error": "Supabase is not configured"}, status_code=500
        )

    try:
        body = await request.json()
        message_text = (body.get("message") or "").strip()
        if not message_text:
            return JSONResponse(
                content={"error": "Message text cannot be empty"}, status_code=400
            )

        # 1. Fetch conversation and contact
        conv_res = (
            supabase.table("whatsapp_conversations")
            .select(
                "id, contact_id, status, whatsapp_contacts(id, phone_number, name)"
            )
            .eq("id", conversation_id)
            .limit(1)
            .execute()
        )

        if not conv_res.data:
            return JSONResponse(
                content={"error": "Conversation not found"}, status_code=404
            )

        conv = conv_res.data[0]
        contact = conv.get("whatsapp_contacts") or {}
        raw_phone = contact.get("phone_number") or ""
        contact_name = contact.get("name") or "Valued Customer"

        # Normalize phone
        clean_phone = re.sub(r"[^\d]", "", str(raw_phone))
        if clean_phone.startswith("0") and len(clean_phone) == 11:
            clean_phone = "92" + clean_phone[1:]
        elif clean_phone.startswith("00"):
            clean_phone = clean_phone[2:]

        if not clean_phone:
            return JSONResponse(
                content={"error": "Invalid recipient phone number"}, status_code=400
            )

        # 2. Check 24-hour window from the last incoming message
        last_incoming = (
            supabase.table("whatsapp_messages")
            .select("created_at")
            .eq("conversation_id", conversation_id)
            .eq("direction", "incoming")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        within_24h = False
        if last_incoming.data:
            last_time_str = last_incoming.data[0].get("created_at")
            if last_time_str:
                try:
                    last_dt = datetime.fromisoformat(
                        last_time_str.replace("Z", "+00:00")
                    )
                    diff = (datetime.now(timezone.utc) - last_dt).total_seconds()
                    if diff <= 86400:
                        within_24h = True
                except Exception as ex:
                    print("Error checking 24h window:", ex)

        # 3. Attempt sending:
        api_response = None
        method_used = "text"

        if within_24h:
            # Try standard text message
            api_response = send_whatsapp_text(clean_phone, message_text)
            if not api_response.ok:
                err_text = api_response.text
                if "131047" in err_text or "24 hours" in err_text.lower():
                    method_used = "template"
                    api_response = send_whatsapp_template(
                        clean_phone,
                        template_name="worknest_agent_message",
                        language_code="en_US",
                        body_parameters=[contact_name, message_text],
                    )
        else:
            # Outside 24 hours: send via pre-approved template
            method_used = "template"
            api_response = send_whatsapp_template(
                clean_phone,
                template_name="worknest_agent_message",
                language_code="en_US",
                body_parameters=[contact_name, message_text],
            )

        if not api_response.ok:
            err_data = {}
            try:
                err_data = api_response.json()
            except Exception:
                err_data = {"raw": api_response.text}

            fb_error = err_data.get("error", {})
            fb_msg = fb_error.get("message", "Failed to send message via WhatsApp")
            fb_code = fb_error.get("code")

            if "template" in fb_msg.lower() or fb_code in (100, 132000, 132001, 132015):
                fb_msg = (
                    "Template 'worknest_agent_message' is still under review or pending approval by Meta. "
                    "Once approved, messages outside the 24-hour window will be delivered."
                )

            return JSONResponse(
                content={
                    "error": fb_msg,
                    "details": err_data,
                    "within_24h": within_24h,
                },
                status_code=400,
            )

        # 4. Message successfully sent - Save to database
        res_json = api_response.json()
        messages_list = res_json.get("messages", [])
        wamid = messages_list[0].get("id") if messages_list else None

        saved = (
            supabase.table("whatsapp_messages")
            .insert({
                "conversation_id": conversation_id,
                "whatsapp_message_id": wamid,
                "direction": "outgoing",
                "message_type": method_used,
                "message_text": message_text,
            })
            .execute()
        )

        supabase.table("whatsapp_conversations").update({
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", conversation_id).execute()

        new_msg = (
            saved.data[0]
            if saved.data
            else {
                "id": None,
                "conversation_id": conversation_id,
                "whatsapp_message_id": wamid,
                "direction": "outgoing",
                "message_type": method_used,
                "message_text": message_text,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )

        return {
            "success": True,
            "method": method_used,
            "message": new_msg,
        }

    except Exception as e:
        print("Manual send message error:", e)
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/conversations/start-or-get")
async def start_or_get_conversation(request: Request):

    if not supabase:
        return JSONResponse(
            content={"error": "Supabase is not configured"}, status_code=500
        )

    try:
        body = await request.json()
        raw_phone = (body.get("phone_number") or "").strip()
        name = (body.get("name") or "").strip() or None

        clean_phone = re.sub(r"[^\d]", "", raw_phone)
        if clean_phone.startswith("0") and len(clean_phone) == 11:
            clean_phone = "92" + clean_phone[1:]
        elif clean_phone.startswith("00"):
            clean_phone = clean_phone[2:]

        if not clean_phone:
            return JSONResponse(
                content={"error": "Valid phone number is required"}, status_code=400
            )

        # Find or create contact
        contact_res = (
            supabase.table("whatsapp_contacts")
            .select("id, phone_number, name")
            .eq("phone_number", clean_phone)
            .limit(1)
            .execute()
        )

        if contact_res.data:
            contact = contact_res.data[0]
            contact_id = contact["id"]
            if name and not contact.get("name"):
                supabase.table("whatsapp_contacts").update({"name": name}).eq(
                    "id", contact_id
                ).execute()
        else:
            new_c = (
                supabase.table("whatsapp_contacts")
                .insert({"phone_number": clean_phone, "name": name})
                .execute()
            )
            contact_id = new_c.data[0]["id"]

        # Find or create open conversation
        conv_res = (
            supabase.table("whatsapp_conversations")
            .select("id, contact_id, status")
            .eq("contact_id", contact_id)
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )

        if conv_res.data:
            conv_id = conv_res.data[0]["id"]
        else:
            new_conv = (
                supabase.table("whatsapp_conversations")
                .insert({"contact_id": contact_id, "status": "open"})
                .execute()
            )
            conv_id = new_conv.data[0]["id"]

        return {"success": True, "conversation_id": conv_id}

    except Exception as e:
        print("Start conversation error:", e)
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/broadcast")
async def send_broadcast(request: Request):
    if not supabase:
        return JSONResponse(
            content={"error": "Supabase is not configured"}, status_code=500
        )

    try:
        body = await request.json()
        message_text = (body.get("message") or "").strip()
        target_tag = (body.get("tag") or "all").strip()

        if not message_text:
            return JSONResponse(
                content={"error": "Broadcast message cannot be empty"}, status_code=400
            )

        conv_res = (
            supabase.table("whatsapp_conversations")
            .select(
                "id, contact_id, status, whatsapp_contacts(id, phone_number, name, tags)"
            )
            .execute()
        )

        all_conversations = conv_res.data or []
        target_items = []
        seen_phones = set()

        for c in all_conversations:
            contact = c.get("whatsapp_contacts") or {}
            raw_phone = contact.get("phone_number") or ""
            clean_phone = re.sub(r"[^\d]", "", str(raw_phone))
            if clean_phone.startswith("0") and len(clean_phone) == 11:
                clean_phone = "92" + clean_phone[1:]
            elif clean_phone.startswith("00"):
                clean_phone = clean_phone[2:]

            if not clean_phone or clean_phone in seen_phones:
                continue

            contact_tags = (contact.get("tags") or "").lower()
            if target_tag != "all":
                if target_tag.lower() not in contact_tags:
                    continue

            seen_phones.add(clean_phone)
            target_items.append(
                {
                    "conversation_id": c.get("id"),
                    "contact_id": contact.get("id"),
                    "phone": clean_phone,
                    "name": contact.get("name") or "Valued Customer",
                }
            )

        if not target_items:
            return JSONResponse(
                content={
                    "error": f"No contacts found for the selected audience ('{target_tag}')"
                },
                status_code=400,
            )

        sent_count = 0
        failed_count = 0
        results = []

        for item in target_items:
            phone = item["phone"]
            conv_id = item["conversation_id"]
            name = item["name"]

            # Check 24h window
            last_incoming = (
                supabase.table("whatsapp_messages")
                .select("created_at")
                .eq("conversation_id", conv_id)
                .eq("direction", "incoming")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )

            within_24h = False
            if last_incoming.data:
                last_time_str = last_incoming.data[0].get("created_at")
                if last_time_str:
                    try:
                        last_dt = datetime.fromisoformat(
                            last_time_str.replace("Z", "+00:00")
                        )
                        diff = (
                            datetime.now(timezone.utc) - last_dt
                        ).total_seconds()
                        if diff <= 86400:
                            within_24h = True
                    except Exception:
                        pass

            method_used = "text"
            api_response = None

            if within_24h:
                api_response = send_whatsapp_text(phone, message_text)
                if not api_response.ok:
                    err_text = api_response.text
                    if "131047" in err_text or "24 hours" in err_text.lower():
                        method_used = "template"
                        api_response = send_whatsapp_template(
                            phone,
                            template_name="worknest_agent_message",
                            language_code="en_US",
                            body_parameters=[name, message_text],
                        )
            else:
                method_used = "template"
                api_response = send_whatsapp_template(
                    phone,
                    template_name="worknest_agent_message",
                    language_code="en_US",
                    body_parameters=[name, message_text],
                )

            if api_response and api_response.ok:
                sent_count += 1
                res_json = {}
                try:
                    res_json = api_response.json()
                except Exception:
                    pass
                messages_list = res_json.get("messages", [])
                wamid = messages_list[0].get("id") if messages_list else None

                supabase.table("whatsapp_messages").insert(
                    {
                        "conversation_id": conv_id,
                        "whatsapp_message_id": wamid,
                        "direction": "outgoing",
                        "message_type": method_used,
                        "message_text": message_text,
                    }
                ).execute()

                supabase.table("whatsapp_conversations").update(
                    {"updated_at": utc_now()}
                ).eq("id", conv_id).execute()

                results.append(
                    {
                        "phone": phone,
                        "name": name,
                        "status": "sent",
                        "method": method_used,
                    }
                )
            else:
                failed_count += 1
                err_text = api_response.text if api_response else "Failed to send"
                results.append(
                    {
                        "phone": phone,
                        "name": name,
                        "status": "failed",
                        "error": err_text[:120],
                    }
                )

            time.sleep(0.1)

        return {
            "success": True,
            "total": len(target_items),
            "sent": sent_count,
            "failed": failed_count,
            "results": results,
        }

    except Exception as e:
        print("Broadcast error:", e)
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ============================================================
# Dashboard - Complaints API
# ============================================================


@app.get("/api/complaints")
async def get_complaints():

    if not supabase:

        return JSONResponse(
            content={"error": "Supabase is not configured"}, status_code=500
        )

    try:

        result = (
            supabase.table("complaints")
            .select(
                "id,"
                "complaint_id,"
                "complaint,"
                "category,"
                "status,"
                "contact_id,"
                "conversation_id,"
                "is_followup,"
                "previous_complaint_id,"
                "created_at,"
                "updated_at,"
                "whatsapp_contacts("
                "phone_number,"
                "name"
                ")"
            )
            .order("created_at", desc=True)
            .execute()
        )

        complaints = []

        for complaint in result.data or []:

            contact = complaint.get("whatsapp_contacts") or {}

            complaints.append(
                {
                    "id": complaint.get("id"),
                    "complaint_id": complaint.get("complaint_id"),
                    "complaint": complaint.get("complaint"),
                    "category": complaint.get("category"),
                    "status": complaint.get("status"),
                    "contact_id": complaint.get("contact_id"),
                    "conversation_id": complaint.get("conversation_id"),
                    "is_followup": complaint.get("is_followup", False),
                    "previous_complaint_id": complaint.get("previous_complaint_id"),
                    "phone_number": contact.get("phone_number"),
                    "name": contact.get("name"),
                    "created_at": complaint.get("created_at"),
                    "updated_at": complaint.get("updated_at"),
                }
            )

        return complaints

    except Exception as e:

        print("Complaints API error:", e)

        return JSONResponse(content={"error": str(e)}, status_code=500)


# ============================================================
# Dashboard - Update Complaint Status
# ============================================================


@app.patch("/api/complaints/{complaint_id}/status")
async def update_complaint_status(complaint_id: str, request: Request):

    if not supabase:

        return JSONResponse(
            {"error": "Supabase is not configured"},
            status_code=500,
        )

    body = await request.json()
    new_status = body.get("status")

    allowed_statuses = {
        "open",
        "in_progress",
        "resolved",
        "closed",
    }

    if new_status not in allowed_statuses:
        return JSONResponse(
            {"error": "Invalid status"},
            status_code=400,
        )

    # Get current complaint
    result = (
        supabase.table("complaints")
        .select("*")
        .eq("complaint_id", complaint_id)
        .single()
        .execute()
    )

    complaint = result.data

    if not complaint:
        return JSONResponse(
            {"error": "Complaint not found"},
            status_code=404,
        )

    old_status = complaint.get("status")

    # Update complaint status
    update_data = {
        "status": new_status,
        "updated_at": utc_now(),
    }

    updated = (
        supabase.table("complaints")
        .update(update_data)
        .eq("complaint_id", complaint_id)
        .execute()
    )

    updated_complaint = updated.data[0] if updated.data else complaint

    should_send_notification = (
        new_status == "resolved"
        and old_status != "resolved"
        and not complaint.get("resolved_notification_sent", False)
    )

    notification_sent = False
    notification_error = None

    if should_send_notification:
        contact_id = complaint.get("contact_id")

        if contact_id:
            contact_result = (
                supabase.table("whatsapp_contacts")
                .select("phone_number")
                .eq("id", contact_id)
                .limit(1)
                .execute()
            )

            if contact_result.data:
                phone_number = contact_result.data[0].get("phone_number")

                if phone_number:
                    try:
                        response = send_resolved_notification(
                            phone_number, complaint_id
                        )

                        if response and response.ok:
                            notification_sent = True

                            supabase.table("complaints").update(
                                {
                                    "resolved_notification_sent": True,
                                    "resolved_notification_sent_at": utc_now(),
                                    "auto_close_at": auto_close_time(),
                                    "resolution_response": None,
                                    "resolution_response_at": None,
                                    "updated_at": utc_now(),
                                }
                            ).eq("complaint_id", complaint_id).execute()

                        else:
                            if response:
                                notification_error = (
                                    "WhatsApp API returned "
                                    f"{response.status_code}: {response.text}"
                                )
                            else:
                                notification_error = "WhatsApp API request failed."

                    except Exception as e:
                        notification_error = str(e)
                        print(
                            "Resolved notification error:",
                            notification_error,
                        )

    return {
        "success": True,
        "complaint": updated_complaint,
    }


# ============================================================
# Dashboard - Bookings API
# ============================================================


@app.get("/api/bookings")
async def get_bookings():

    if not supabase:
        return JSONResponse(
            {"error": "Supabase is not configured"},
            status_code=500,
        )

    try:
        result = (
            supabase.table("bookings")
            .select(
                "id,"
                "booking_id,"
                "workspace_type,"
                "customer_name,"
                "customer_phone,"
                "seats,"
                "status,"
                "notes,"
                "contact_id,"
                "conversation_id,"
                "created_at,"
                "updated_at,"
                "whatsapp_contacts("
                "phone_number,"
                "name"
                ")"
            )
            .order("created_at", desc=True)
            .execute()
        )

        bookings = []

        for booking in result.data or []:
            contact = booking.get("whatsapp_contacts") or {}

            bookings.append(
                {
                    "id": booking.get("id"),
                    "booking_id": booking.get("booking_id"),
                    "workspace_type": booking.get("workspace_type"),
                    "customer_name": booking.get("customer_name") or contact.get("name"),
                    "customer_phone": booking.get("customer_phone") or contact.get("phone_number"),
                    "whatsapp_phone": contact.get("phone_number"),
                    "seats": booking.get("seats"),
                    "status": booking.get("status", "pending"),
                    "notes": booking.get("notes"),
                    "contact_id": booking.get("contact_id"),
                    "conversation_id": booking.get("conversation_id"),
                    "created_at": booking.get("created_at"),
                    "updated_at": booking.get("updated_at"),
                }
            )

        return bookings

    except Exception as e:
        print("Bookings API error:", e)
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.patch("/api/bookings/{booking_id}/status")
async def update_booking_status(booking_id: str, request: Request):

    if not supabase:
        return JSONResponse(
            {"error": "Supabase is not configured"},
            status_code=500,
        )

    body = await request.json()
    new_status = body.get("status")

    allowed_statuses = {
        "pending",
        "confirmed",
        "cancelled",
        "completed",
    }

    if new_status not in allowed_statuses:
        return JSONResponse(
            {
                "error": (
                    f"Invalid status '{new_status}'. Allowed values: "
                    f"{', '.join(sorted(allowed_statuses))}"
                )
            },
            status_code=400,
        )

    result = (
        supabase.table("bookings")
        .update({"status": new_status, "updated_at": utc_now()})
        .eq("booking_id", booking_id)
        .execute()
    )

    updated_booking = result.data[0]

    # Send WhatsApp notification to customer if confirmed or cancelled
    if new_status in {"confirmed", "cancelled"}:
        try:
            target_phone = None

            # 1. Prefer the verified WhatsApp phone from whatsapp_contacts (always has country code)
            if updated_booking.get("contact_id"):
                contact_res = (
                    supabase.table("whatsapp_contacts")
                    .select("phone_number")
                    .eq("id", updated_booking["contact_id"])
                    .limit(1)
                    .execute()
                )
                if contact_res.data:
                    target_phone = contact_res.data[0].get("phone_number")

            # 2. Fallback to customer_phone normalized with country code
            if not target_phone and updated_booking.get("customer_phone"):
                raw = re.sub(r"[^\d+]", "", updated_booking["customer_phone"])
                if raw.startswith("+"):
                    target_phone = raw[1:]
                elif raw.startswith("0") and len(raw) == 11:
                    target_phone = "92" + raw[1:]
                else:
                    target_phone = raw

            if target_phone:
                cust_name = updated_booking.get("customer_name") or "Valued Customer"
                ws = updated_booking.get("workspace_type") or "Workspace"

                if new_status == "confirmed":
                    msg = (
                        "WorkNest Booking Confirmed\n\n"
                        f"Dear {cust_name},\n"
                        f"Your booking {booking_id} for {ws} has been confirmed.\n\n"
                        "We look forward to welcoming you at WorkNest!\n"
                        "If you have any questions, feel free to reply to this chat."
                    )
                else:
                    msg = (
                        "WorkNest Booking Update\n\n"
                        f"Dear {cust_name},\n"
                        f"Your booking {booking_id} has been cancelled.\n\n"
                        "If you wish to make a new booking, type 0 to view the main menu."
                    )

                sent = send_whatsapp_message(target_phone, msg)

                # Record outgoing notification in conversation history
                if sent and updated_booking.get("conversation_id"):
                    save_message(
                        conversation_id=updated_booking["conversation_id"],
                        direction="outgoing",
                        message_text=msg,
                        message_type="text",
                    )

        except Exception as e:
            print("Failed to send booking notification via WhatsApp:", e)

    return {
        "success": True,
        "booking": updated_booking,
    }


# ============================================================
# Dashboard Authentication
# ============================================================


def dashboard_login_page():

    return HTMLResponse(
        content="""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>WorkNest Admin Login</title>
    <style>
        body {
            margin: 0;
            min-height: 100vh;
            display: grid;
            place-items: center;
            background: #f6f7f9;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            color: #202124;
        }
        form {
            width: min(360px, calc(100% - 40px));
            padding: 28px;
            background: white;
            border: 1px solid #e8e8e8;
            border-radius: 12px;
            box-shadow: 0 12px 30px rgba(0, 0, 0, 0.08);
        }
        h1 { margin: 0 0 8px; font-size: 22px; }
        p { margin: 0 0 22px; color: #73777d; font-size: 14px; }
        label { display: block; margin: 14px 0 6px; font-size: 13px; font-weight: 700; }
        input, button {
            width: 100%;
            height: 42px;
            box-sizing: border-box;
            border-radius: 8px;
            font-size: 14px;
        }
        input { border: 1px solid #d9dce1; padding: 0 12px; }
        button { margin-top: 20px; border: 0; background: #f59e1d; color: white; font-weight: 700; cursor: pointer; }
        #error { min-height: 18px; margin-top: 12px; color: #c62828; font-size: 13px; }
    </style>
</head>
<body>
    <form id="loginForm">
        <h1>WorkNest Admin</h1>
        <p>Sign in to manage WhatsApp complaints.</p>
        <label for="userName">Username</label>
        <input id="userName" name="user_name" type="text" autocomplete="username" required>
        <label for="password">Password</label>
        <input id="password" name="password" type="password" autocomplete="current-password" required>
        <button type="submit">Sign in</button>
        <div id="error" role="alert"></div>
    </form>
    <script>
        document.getElementById("loginForm").addEventListener("submit", async (event) => {
            event.preventDefault();
            const response = await fetch("/dashboard/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    user_name: document.getElementById("userName").value,
                    password: document.getElementById("password").value
                })
            });
            if (response.ok) {
                window.location.href = "/dashboard";
                return;
            }
            document.getElementById("error").textContent = "Invalid username or password.";
        });
    </script>
</body>
</html>
""",
        status_code=200,
    )


@app.middleware("http")
async def dashboard_authentication(request: Request, call_next):

    protected_path = request.url.path == "/dashboard" or request.url.path.startswith(
        "/api/"
    )

    if protected_path:
        user_name = get_dashboard_session_user_name(
            request.cookies.get("dashboard_session")
        )

        if not user_name:
            if request.url.path == "/dashboard":
                return dashboard_login_page()

            return JSONResponse(
                {"error": "Authentication required"},
                status_code=401,
            )

    return await call_next(request)


@app.get("/dashboard/login", response_class=HTMLResponse)
async def dashboard_login():
    return dashboard_login_page()


@app.post("/dashboard/login")
async def dashboard_login_submit(request: Request):

    if not supabase:
        return JSONResponse(
            {"error": "Supabase is not configured"},
            status_code=500,
        )

    body = await request.json()
    user_name = (body.get("user_name") or "").strip()
    password = body.get("password") or ""

    if not user_name or not password:
        return JSONResponse(
            {"error": "Username and password are required"},
            status_code=400,
        )

    result = (
        supabase.table("dashboard_users")
        .select("user_name,password_hash,password_salt")
        .eq("user_name", user_name)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )

    if not result.data:
        return JSONResponse({"error": "Invalid credentials"}, status_code=401)

    user = result.data[0]
    _, password_hash = make_password_hash(password, user["password_salt"])

    if not hmac.compare_digest(password_hash, user["password_hash"]):
        return JSONResponse({"error": "Invalid credentials"}, status_code=401)

    response = JSONResponse({"success": True})
    response.set_cookie(
        "dashboard_session",
        make_dashboard_session(user["user_name"]),
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=8 * 60 * 60,
    )
    return response


@app.post("/dashboard/logout")
async def dashboard_logout():
    response = JSONResponse({"success": True})
    response.delete_cookie("dashboard_session")
    return response


# ============================================================
# Dashboard
# ============================================================


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():

    html = r"""
<!DOCTYPE html>

<html lang="en">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<title>WorkNest Admin Dashboard</title>

<style>

:root {
    --orange: #F59E1D;
    --orange-dark: #D98208;
    --orange-light: #FFF1D8;

    --bg: #F6F7F9;
    --white: #FFFFFF;

    --border: #E8E8E8;

    --text: #202124;
    --muted: #73777D;

    --green: #198754;
    --red: #DC3545;
    --blue: #2563EB;

    --sidebar-width: 360px;
}

* {
    box-sizing: border-box;
}

body {
    margin: 0;

    font-family:
        Inter,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;

    background: var(--bg);
    color: var(--text);
}

.app {
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

.top-nav {
    height: 64px;
    background: var(--white);
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 20px;
    flex-shrink: 0;
    gap: 16px;
    z-index: 100;
}

.top-nav-left {
    display: flex;
    align-items: center;
    min-width: 200px;
}

.top-kpi-bar {
    display: flex;
    align-items: center;
    gap: 10px;
    overflow-x: auto;
    padding: 4px 0;
}

.kpi-card {
    background: #FAFAFA;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 6px 14px;
    display: flex;
    flex-direction: column;
    min-width: 125px;
    cursor: pointer;
    transition: all 0.15s ease;
}

.kpi-card:hover {
    background: #FFF7ED;
    border-color: var(--orange);
    transform: translateY(-1px);
}

.kpi-label {
    font-size: 10px;
    font-weight: 700;
    color: var(--muted);
    white-space: nowrap;
    text-transform: uppercase;
    letter-spacing: 0.4px;
}

.kpi-value {
    font-size: 16px;
    font-weight: 800;
    color: var(--text);
    margin-top: 2px;
}

.kpi-warning {
    color: var(--orange-dark);
}

.kpi-success {
    color: var(--green);
}

.kpi-danger {
    color: var(--red);
}

.top-nav-right {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
}

.nav-control-btn {
    background: #F3F4F6;
    border: 1px solid var(--border);
    color: #374151;
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 11px;
    font-weight: 700;
    cursor: pointer;
    transition: all 0.15s;
    white-space: nowrap;
}

.nav-control-btn:hover {
    background: #E5E7EB;
}

.nav-control-btn.active {
    background: var(--orange-light);
    color: var(--orange-dark);
    border-color: var(--orange);
}

.nav-control-btn.logout-btn:hover {
    background: #FEE2E2;
    color: #DC2626;
    border-color: #F87171;
}

.main-workspace {
    flex: 1;
    display: flex;
    min-height: 0;
    overflow: hidden;
}

/* =========================================================
   Sidebar
   ========================================================= */

.sidebar {
    width: var(--sidebar-width);
    background: var(--white);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
}

.brand-logo {
    width: 36px;
    height: 36px;
    border-radius: 10px;
    background: var(--orange);
    display: flex;
    align-items: center;
    justify-content: center;
    color: white;
    font-size: 17px;
    font-weight: 800;
}

.brand-text {
    margin-left: 10px;
}

.brand-title {
    font-size: 16px;
    font-weight: 800;
}

.brand-subtitle {
    color: var(--muted);
    font-size: 11px;
    margin-top: 1px;
}

.tabs {
    display: flex;

    padding: 10px;

    gap: 5px;

    border-bottom:
        1px solid var(--border);
}

.tab {
    flex: 1;

    border: 0;

    background: transparent;

    padding: 9px 4px;

    border-radius: 8px;

    cursor: pointer;

    font-size: 12px;

    font-weight: 700;

    color: var(--muted);

    text-align: center;

    white-space: nowrap;
}

.tab.active {
    background:
        var(--orange-light);

    color:
        var(--orange-dark);
}

.tab-alert {
    display: none;

    margin-left: 5px;

    min-width: 16px;

    padding: 2px 5px;

    border-radius: 10px;

    background: var(--red);

    color: white;

    font-size: 10px;

    line-height: 1.2;
}

.sidebar-header {
    padding: 16px;

    border-bottom:
        1px solid var(--border);
}

.sidebar-title-row {
    display: flex;

    align-items: center;

    justify-content:
        space-between;

    margin-bottom: 12px;
}

.sidebar-title {
    font-size: 16px;

    font-weight: 750;
}

.count {
    background:
        var(--orange-light);

    color:
        var(--orange-dark);

    padding:
        4px 9px;

    border-radius:
        20px;

    font-size: 12px;

    font-weight: 700;
}

.search {
    width: 100%;

    height: 42px;

    border:
        1px solid var(--border);

    border-radius: 10px;

    padding:
        0 13px;

    outline: none;

    font-size: 14px;
}

.search:focus {
    border-color:
        var(--orange);
}

.complaint-notification {
    display: none;

    margin-top: 10px;

    padding: 9px 11px;

    border-radius: 8px;

    background: #FFF1D8;

    color: var(--orange-dark);

    font-size: 12px;

    font-weight: 700;
}

/* =========================================================
   Conversations
   ========================================================= */

.list {
    flex: 1;

    overflow-y: auto;
}

.conversation {
    padding:
        14px 16px;

    display: flex;

    gap: 12px;

    cursor: pointer;

    border-bottom:
        1px solid #F2F2F2;

    transition:
        0.15s;
}

.conversation:hover {
    background:
        #FAFAFA;
}

.conversation.active {
    background:
        #FFF7E9;

    border-left:
        3px solid var(--orange);

    padding-left:
        13px;
}

.avatar {
    width: 43px;
    height: 43px;

    flex-shrink: 0;

    border-radius:
        50%;

    background:
        #E9EAEC;

    display: flex;

    align-items: center;
    justify-content: center;

    color: #555;

    font-weight: 750;
}

.conversation-info {
    min-width: 0;

    flex: 1;
}

.conversation-top {
    display: flex;

    justify-content:
        space-between;

    gap: 10px;
}

.conversation-name {
    font-size: 14px;

    font-weight: 700;

    white-space: nowrap;

    overflow: hidden;

    text-overflow: ellipsis;
}

.conversation-date {
    font-size: 11px;

    color: var(--muted);

    white-space: nowrap;
}

.conversation-phone {
    color: var(--muted);

    font-size: 12px;

    margin-top: 4px;
}

/* =========================================================
   Chat
   ========================================================= */

.chat {
    flex: 1;

    display: flex;

    flex-direction: column;

    min-width: 0;
}

.chat-header {
    height: 72px;

    background:
        var(--white);

    border-bottom:
        1px solid var(--border);

    display: flex;

    align-items: center;

    padding:
        0 25px;
}

.chat-avatar {
    width: 43px;
    height: 43px;

    border-radius:
        50%;

    background:
        var(--orange);

    color: white;

    display: flex;

    align-items: center;
    justify-content: center;

    font-weight: 800;
}

.chat-info {
    margin-left: 12px;
}

.chat-name {
    font-size: 15px;

    font-weight: 750;
}

.chat-phone {
    font-size: 12px;

    color: var(--muted);

    margin-top: 3px;
}

.messages {
    flex: 1;

    overflow-y: auto;

    padding: 25px;

    display: flex;

    flex-direction: column;

    gap: 9px;

    background:
        var(--bg);
}

.message-row {
    display: flex;
}

.message-row.incoming {
    justify-content:
        flex-start;
}

.message-row.outgoing {
    justify-content:
        flex-end;
}

.message {
    max-width: 70%;

    padding:
        10px 13px;

    border-radius:
        12px;

    font-size: 14px;

    line-height: 1.5;

    box-shadow:
        0 1px 2px
        rgba(0,0,0,0.04);
}

.message.incoming {
    background:
        var(--white);

    border:
        1px solid var(--border);

    border-top-left-radius:
        4px;
}

.message.outgoing {
    background:
        var(--orange-light);

    border-top-right-radius:
        4px;
}

.message-time {
    margin-top: 5px;

    font-size: 10px;

    color: var(--muted);

    text-align:
        right;
}

/* =========================================================
   Chat Composer & Manual Messaging
   ========================================================= */

.chat-composer {
    padding: 14px 20px;
    background: var(--white);
    border-top: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    gap: 8px;
    flex-shrink: 0;
}

.chat-composer-row {
    display: flex;
    gap: 10px;
    align-items: flex-end;
}

.chat-composer-textarea {
    flex: 1;
    min-height: 44px;
    max-height: 120px;
    padding: 10px 14px;
    border: 1px solid var(--border);
    border-radius: 8px;
    font-family: inherit;
    font-size: 14px;
    resize: none;
    line-height: 1.4;
    outline: none;
    background: #FAFAFA;
    box-sizing: border-box;
    transition: border-color 0.2s;
}

.chat-composer-textarea:focus {
    border-color: var(--orange);
    background: #FFFFFF;
}

.chat-composer-btn {
    padding: 0 20px;
    height: 44px;
    background: var(--orange);
    color: white;
    border: none;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 700;
    cursor: pointer;
    transition: background 0.2s, opacity 0.2s;
    display: flex;
    align-items: center;
    justify-content: center;
    white-space: nowrap;
}

.chat-composer-btn:hover {
    background: var(--orange-dark);
}

.chat-composer-btn:disabled {
    opacity: 0.55;
    cursor: not-allowed;
}

.chat-composer-footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 11px;
    color: var(--muted);
}

.new-chat-btn {
    background: var(--orange);
    color: white;
    border: none;
    border-radius: 6px;
    padding: 5px 11px;
    font-size: 11px;
    font-weight: 700;
    cursor: pointer;
    transition: background 0.2s;
    white-space: nowrap;
}

.new-chat-btn:hover {
    background: var(--orange-dark);
}

.modal-overlay {
    position: fixed;
    top: 0;
    left: 0;
    width: 100vw;
    height: 100vh;
    background: rgba(0, 0, 0, 0.45);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 9999;
}

.modal-box {
    background: #FFFFFF;
    border-radius: 12px;
    width: 90%;
    max-width: 480px;
    padding: 24px;
    box-shadow: 0 10px 25px rgba(0, 0, 0, 0.15);
}

.modal-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid var(--border);
    padding-bottom: 12px;
}

/* =========================================================
   Tags, Contact Meta & Export
   ========================================================= */

.tag-badge {
    display: inline-block;
    padding: 2px 7px;
    border-radius: 10px;
    font-size: 9px;
    font-weight: 700;
    margin-right: 4px;
    margin-top: 4px;
    letter-spacing: 0.3px;
    text-transform: capitalize;
}

.tag-hot-lead {
    background: #FEE2E2;
    color: #DC2626;
    border: 1px solid #FECACA;
}

.tag-corporate {
    background: #EDE9FE;
    color: #7C3AED;
    border: 1px solid #DDD6FE;
}

.tag-day-pass {
    background: #FEF3C7;
    color: #D97706;
    border: 1px solid #FDE68A;
}

.tag-active-member {
    background: #D1FAE5;
    color: #059669;
    border: 1px solid #A7F3D0;
}

.contact-meta-box {
    background: #FFFFFF;
    border-bottom: 1px solid var(--border);
    padding: 10px 24px;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
}

.tag-chip {
    padding: 4px 10px;
    border-radius: 16px;
    font-size: 11px;
    font-weight: 700;
    cursor: pointer;
    background: #F3F4F6;
    color: #4B5563;
    border: 1px solid #E5E7EB;
    transition: all 0.15s;
    user-select: none;
}

.tag-chip:hover {
    filter: brightness(0.95);
}

.tag-chip.selected-hot-lead {
    background: #DC2626;
    color: #FFFFFF;
    border-color: #DC2626;
}

.tag-chip.selected-corporate {
    background: #7C3AED;
    color: #FFFFFF;
    border-color: #7C3AED;
}

.tag-chip.selected-day-pass {
    background: #D97706;
    color: #FFFFFF;
    border-color: #D97706;
}

.tag-chip.selected-active-member {
    background: #059669;
    color: #FFFFFF;
    border-color: #059669;
}

.admin-notes-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: 1;
    min-width: 250px;
}

.admin-notes-input {
    flex: 1;
    height: 32px;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0 10px;
    font-size: 12px;
    background: #FAFAFA;
    outline: none;
}

.admin-notes-input:focus {
    border-color: var(--orange);
    background: #FFFFFF;
}

.save-note-btn {
    height: 32px;
    padding: 0 12px;
    background: #374151;
    color: #FFFFFF;
    border: none;
    border-radius: 6px;
    font-size: 11px;
    font-weight: 700;
    cursor: pointer;
    transition: background 0.15s;
    white-space: nowrap;
}

.save-note-btn:hover {
    background: #1F2937;
}

.export-btn {
    background: #198754;
    color: #FFFFFF;
    border: none;
    border-radius: 6px;
    padding: 5px 11px;
    font-size: 11px;
    font-weight: 700;
    cursor: pointer;
    transition: background 0.2s;
    white-space: nowrap;
}

.export-btn:hover {
    background: #157347;
}

/* =========================================================
   Complaints
   ========================================================= */

.complaint {
    padding:
        14px 16px;

    border-bottom:
        1px solid #F2F2F2;

    cursor: pointer;
}

.complaint:hover {
    background:
        #FAFAFA;
}

.complaint-id {
    font-size: 13px;

    font-weight: 800;

    color:
        var(--orange-dark);
}

.complaint-category {
    font-size: 12px;

    color: var(--muted);

    margin-top: 3px;
}

.followup-badge {
    display: inline-block;

    margin-top: 7px;

    padding: 4px 8px;

    border-radius: 20px;

    background: #E8F0FE;

    color: var(--blue);

    font-size: 10px;

    font-weight: 750;
}

.complaint-text {
    font-size: 13px;

    margin-top: 7px;

    line-height: 1.4;
}

.status {
    display: inline-block;

    margin-top: 8px;

    padding:
        4px 8px;

    border-radius:
        20px;

    font-size: 10px;

    font-weight: 750;

    text-transform:
        capitalize;
}

.status-open {
    background:
        #FFF1D8;

    color:
        var(--orange-dark);
}

.status-in_progress {
    background:
        #E8F0FE;

    color:
        var(--blue);
}

.status-resolved {
    background:
        #E8F7EF;

    color:
        var(--green);
}

.status-closed {
    background:
        #EEEEEE;

    color:
        #555;
}

.status-pending {
    background: #FFF1D8;
    color: var(--orange-dark);
}

.status-confirmed {
    background: #E8F7EF;
    color: var(--green);
}

.status-cancelled {
    background: #FEECEE;
    color: var(--red);
}

.status-completed {
    background: #E8F0FE;
    color: var(--blue);
}

.booking {
    padding: 14px 16px;
    border-bottom: 1px solid #F2F2F2;
    cursor: pointer;
}

.booking:hover {
    background: #FAFAFA;
}

.booking-id {
    font-size: 13px;
    font-weight: 800;
    color: var(--orange-dark);
}

.booking-workspace {
    font-size: 13px;
    font-weight: 700;
    margin-top: 3px;
    color: var(--text);
}

.booking-info {
    font-size: 12px;
    color: var(--muted);
    margin-top: 3px;
}

.booking-select {
    margin-top: 8px;
    width: 100%;
    height: 32px;
    border: 1px solid var(--border);
    border-radius: 7px;
    background: var(--white);
    padding: 0 7px;
    font-size: 12px;
}

.complaint-select {
    margin-top: 8px;

    width: 100%;

    height: 32px;

    border:
        1px solid var(--border);

    border-radius:
        7px;

    background:
        var(--white);

    padding:
        0 7px;

    font-size: 12px;

    outline: none;
}

/* =========================================================
   Toast & Action Buttons
   ========================================================= */

.toast-container {
    position: fixed;
    top: 20px;
    right: 24px;
    z-index: 99999;
    display: flex;
    flex-direction: column;
    gap: 8px;
    pointer-events: none;
}

.toast {
    pointer-events: auto;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 18px;
    background: #1e293b;
    color: #ffffff;
    font-size: 13px;
    font-weight: 600;
    border-radius: 8px;
    box-shadow: 0 10px 25px rgba(0,0,0,0.25);
    animation: toastSlideIn 0.25s cubic-bezier(0.16, 1, 0.3, 1) forwards;
    transition: opacity 0.3s ease, transform 0.3s ease;
}

@keyframes toastSlideIn {
    from { opacity: 0; transform: translateY(-16px) scale(0.95); }
    to { opacity: 1; transform: translateY(0) scale(1); }
}

.toast-success { border-left: 4px solid var(--green); }
.toast-error { border-left: 4px solid var(--red); }
.toast-info { border-left: 4px solid var(--blue); }

.status-btn {
    border: none;
    padding: 10px 18px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 700;
    cursor: pointer;
    transition: all 0.18s ease-in-out;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    position: relative;
    user-select: none;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}

.status-btn:hover:not(:disabled) {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
}

.status-btn:active:not(:disabled) {
    transform: translateY(1px) scale(0.98);
}

.status-btn:disabled {
    opacity: 0.65;
    cursor: not-allowed;
    transform: none !important;
}

.status-btn.current-status {
    box-shadow: 0 0 0 3px rgba(0,0,0,0.12), inset 0 2px 4px rgba(0,0,0,0.1);
    filter: brightness(0.96);
}

.status-btn.current-status::after {
    content: " (Current)";
    font-size: 11px;
    opacity: 0.85;
    font-weight: 600;
}

/* =========================================================
   Empty State
   ========================================================= */

.empty {
    flex: 1;

    display: flex;

    align-items: center;

    justify-content: center;

    text-align: center;

    color:
        var(--muted);
}

.empty-box {
    max-width:
        360px;
}

.empty-title {
    color:
        var(--text);

    font-size:
        20px;

    font-weight:
        800;

    margin-bottom:
        8px;
}

.empty-text {
    font-size:
        14px;

    line-height:
        1.6;
}

/* =========================================================
   Mobile
   ========================================================= */

@media (max-width: 800px) {

    .sidebar {
        width: 100%;
    }

    .chat {
        display: none;
    }

    .sidebar.hidden {
        display: none;
    }

    .chat.mobile-visible {
        display: flex;

        width: 100%;
    }

    .message {
        max-width:
            85%;
    }

}

</style>

</head>

<body>

<div class="toast-container" id="toastContainer"></div>

<div id="newChatModal" class="modal-overlay" style="display: none;" onclick="if(event.target===this) closeNewChatModal()">
    <div class="modal-box">
        <div class="modal-header">
            <h3 style="margin: 0; font-size: 16px; font-weight: 750; color: var(--text);">New WhatsApp Message</h3>
            <button type="button" onclick="closeNewChatModal()" style="background: none; border: none; font-size: 20px; line-height: 1; cursor: pointer; color: var(--muted);">&times;</button>
        </div>
        <div style="margin-top: 16px; display: flex; flex-direction: column; gap: 14px;">
            <div>
                <label style="display: block; font-size: 12px; font-weight: 700; color: var(--muted); margin-bottom: 5px;">Recipient Phone Number *</label>
                <input id="newChatPhone" class="search" placeholder="e.g. 03221234567 or 923221234567" style="height: 38px;">
            </div>
            <div>
                <label style="display: block; font-size: 12px; font-weight: 700; color: var(--muted); margin-bottom: 5px;">Customer Name (Optional)</label>
                <input id="newChatName" class="search" placeholder="Customer name" style="height: 38px;">
            </div>
            <div>
                <label style="display: block; font-size: 12px; font-weight: 700; color: var(--muted); margin-bottom: 5px;">Message *</label>
                <textarea id="newChatMessage" class="chat-composer-textarea" rows="3" placeholder="Type your message here..."></textarea>
            </div>
            <div id="newChatStatus" style="font-size: 12px; font-weight: 600; min-height: 16px;"></div>
            <div style="display: flex; justify-content: flex-end; gap: 10px; margin-top: 6px;">
                <button type="button" onclick="closeNewChatModal()" class="status-btn" style="background: #E5E7EB; color: #374151;">Cancel</button>
                <button type="button" id="newChatSubmitBtn" onclick="submitNewChat()" class="chat-composer-btn" style="height: 38px;">Send Message</button>
            </div>
        </div>
    </div>
</div>

<div id="broadcastModal" class="modal-overlay" style="display: none;" onclick="if(event.target===this) closeBroadcastModal()">
    <div class="modal-box" style="max-width: 560px;">
        <div class="modal-header">
            <div>
                <h3 style="margin: 0; font-size: 16px; font-weight: 750; color: var(--text);">Broadcast Announcement</h3>
                <div style="font-size: 12px; color: var(--muted); margin-top: 2px;">Send an Eid greeting, holiday update, or marketing offer to your contacts</div>
            </div>
            <button type="button" onclick="closeBroadcastModal()" style="background: none; border: none; font-size: 20px; line-height: 1; cursor: pointer; color: var(--muted);">&times;</button>
        </div>
        <div style="margin-top: 16px; display: flex; flex-direction: column; gap: 14px;">
            <div>
                <label style="display: block; font-size: 12px; font-weight: 700; color: var(--muted); margin-bottom: 5px;">Target Audience</label>
                <div style="display: flex; gap: 10px; align-items: center;">
                    <select id="broadcastAudience" class="search" style="height: 38px; flex: 1;" onchange="updateBroadcastRecipientCount()">
                        <option value="all">All Contacts / All Conversations</option>
                        <option value="Hot Lead">Hot Lead only</option>
                        <option value="Corporate">Corporate only</option>
                        <option value="Day Pass">Day Pass only</option>
                        <option value="Active Member">Active Member only</option>
                    </select>
                    <span id="broadcastRecipientCount" style="font-size: 12px; font-weight: 700; color: var(--text); background: #F3F4F6; padding: 8px 12px; border-radius: 6px; white-space: nowrap;">
                        0 recipients
                    </span>
                </div>
            </div>

            <div>
                <label style="display: block; font-size: 12px; font-weight: 700; color: var(--muted); margin-bottom: 5px;">Quick Templates (Click to fill)</label>
                <div style="display: flex; gap: 6px; flex-wrap: wrap;">
                    <button type="button" class="tag-chip" onclick="fillBroadcastTemplate('eid')">Eid Mubarak</button>
                    <button type="button" class="tag-chip" onclick="fillBroadcastTemplate('weekend')">Weekend Pass Offer</button>
                    <button type="button" class="tag-chip" onclick="fillBroadcastTemplate('amenities')">New Amenities</button>
                    <button type="button" class="tag-chip" onclick="fillBroadcastTemplate('hours')">Holiday Hours</button>
                </div>
            </div>

            <div>
                <label style="display: block; font-size: 12px; font-weight: 700; color: var(--muted); margin-bottom: 5px;">Broadcast Message *</label>
                <textarea id="broadcastMessage" class="chat-composer-textarea" rows="4" placeholder="Type your broadcast message here..." oninput="updateBroadcastCharCount()"></textarea>
                <div style="display: flex; justify-content: space-between; font-size: 11px; color: var(--muted); margin-top: 4px;">
                    <span>Message will be saved to each customer's conversation history</span>
                    <span id="broadcastCharCount">0 characters</span>
                </div>
            </div>

            <div style="background: #F8FAFC; border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; font-size: 12px; color: #475569; line-height: 1.45;">
                <strong>Delivery Notice:</strong> Customers active within 24 hours receive direct text. Contacts outside 24 hours are delivered via pre-approved template in accordance with WhatsApp Business policies.
            </div>

            <div style="display: flex; align-items: center; gap: 8px;">
                <input type="checkbox" id="broadcastConfirmCheck" style="width: 16px; height: 16px; cursor: pointer;">
                <label for="broadcastConfirmCheck" style="font-size: 12px; font-weight: 600; color: var(--text); cursor: pointer;">
                    I confirm sending this announcement to all selected contacts
                </label>
            </div>

            <div id="broadcastStatus" style="font-size: 12px; font-weight: 600; min-height: 18px;"></div>

            <div style="display: flex; justify-content: flex-end; gap: 10px; margin-top: 4px;">
                <button type="button" onclick="closeBroadcastModal()" class="status-btn" style="background: #E5E7EB; color: #374151;">Cancel</button>
                <button type="button" id="broadcastSubmitBtn" onclick="submitBroadcast()" class="chat-composer-btn" style="height: 38px;">Send Broadcast</button>
            </div>
        </div>
    </div>
</div>

<div class="app">

    <header class="top-nav">
        <div class="top-nav-left">
            <div class="brand-logo">W</div>
            <div class="brand-text">
                <div class="brand-title">WorkNest</div>
                <div class="brand-subtitle">WhatsApp Admin</div>
            </div>
        </div>

        <div class="top-kpi-bar" id="kpiBar">
            <div class="kpi-card" onclick="showMessages()" title="Click to view messages">
                <span class="kpi-label">Active Chats</span>
                <span class="kpi-value" id="kpiActiveConversations">-</span>
            </div>
            <div class="kpi-card" onclick="showBookings()" title="Click to view bookings">
                <span class="kpi-label">Pending Bookings</span>
                <span class="kpi-value kpi-warning" id="kpiPendingBookings">-</span>
            </div>
            <div class="kpi-card" onclick="showBookings()" title="Click to view bookings">
                <span class="kpi-label">Confirmed (7d)</span>
                <span class="kpi-value kpi-success" id="kpiConfirmedWeek">-</span>
            </div>
            <div class="kpi-card" onclick="showComplaints()" title="Click to view complaints">
                <span class="kpi-label">Open Complaints</span>
                <span class="kpi-value kpi-danger" id="kpiOpenComplaints">-</span>
            </div>
        </div>

        <div class="top-nav-right">
            <button id="broadcastBtn" class="nav-control-btn active" style="background: var(--orange-light); color: var(--orange-dark); border-color: var(--orange);" onclick="openBroadcastModal()" title="Broadcast announcement or marketing message to contacts">
                Broadcast
            </button>
            <button id="soundToggleBtn" class="nav-control-btn active" onclick="toggleSound()" title="Toggle notification sound chime">
                Sound: ON
            </button>
            <button id="notifyToggleBtn" class="nav-control-btn" onclick="requestDesktopNotification()" title="Enable desktop notifications">
                Notifications
            </button>
            <button class="nav-control-btn logout-btn" onclick="logout()" title="Log out">
                Logout
            </button>
        </div>
    </header>

    <div class="main-workspace">

        <aside
            class="sidebar"
            id="sidebar"
        >

            <div class="tabs">

                <button
                    class="tab active"
                    id="messagesTab"
                    onclick="showMessages()"
                >
                    Messages
                </button>

                <button
                    class="tab"
                    id="bookingsTab"
                    onclick="showBookings()"
                >
                    Bookings
                    <span
                        class="tab-alert"
                        id="bookingAlert"
                    ></span>
                </button>

                <button
                    class="tab"
                    id="complaintsTab"
                    onclick="showComplaints()"
                >
                    Complaints
                    <span
                        class="tab-alert"
                        id="complaintAlert"
                    ></span>
                </button>

            </div>


            <div class="sidebar-header">

                <div class="sidebar-title-row">

                    <div style="display: flex; align-items: center; gap: 8px;">
                        <div
                            class="sidebar-title"
                            id="listTitle"
                        >
                            Conversations
                        </div>

                        <div
                            class="count"
                            id="itemCount"
                        >
                            0
                        </div>
                    </div>

                    <div style="display: flex; align-items: center; gap: 6px;">
                        <button
                            id="sidebarBroadcastBtn"
                            class="export-btn"
                            style="background: #FFF7ED; color: var(--orange-dark); border-color: var(--orange);"
                            onclick="openBroadcastModal()"
                            title="Send broadcast marketing or holiday greeting message"
                        >
                            Broadcast
                        </button>
                        <button
                            id="newChatBtn"
                            class="new-chat-btn"
                            onclick="openNewChatModal()"
                            title="Start new conversation with any phone number"
                        >
                            + New Message
                        </button>
                        <button
                            id="exportCsvBtn"
                            class="export-btn"
                            style="display: none;"
                            onclick="exportCurrentViewCsv()"
                            title="Download CSV report"
                        >
                            Export CSV
                        </button>
                    </div>

                </div>

                <input
                    id="search"
                    class="search"
                    type="text"
                    placeholder="Search..."
                    autocomplete="off"
                >

                <div
                    class="complaint-notification"
                    id="complaintNotification"
                    role="status"
                ></div>

            </div>


            <div
                class="list"
                id="list"
            ></div>

        </aside>


        <main
            class="chat"
            id="chat"
        >

            <div
                class="empty"
                id="emptyState"
            >

                <div class="empty-box">

                    <div class="empty-title">
                        WorkNest WhatsApp
                    </div>

                    <div class="empty-text">
                        Select a conversation from the
                        left to view the complete
                        message history.
                    </div>

                </div>

            </div>

        </main>

    </div>

</div>


<script>

let conversations = [];
let complaints = [];
let bookings = [];

let knownComplaintIds = new Set();
let knownBookingIds = new Set();
let knownConversationUpdateTimes = new Map();

let hasLoadedComplaints = false;
let hasLoadedBookings = false;
let hasLoadedConversations = false;

let selectedConversation = null;
let currentView = "messages";

let soundEnabled = true;
let desktopNotifyEnabled = (typeof Notification !== "undefined" && Notification.permission === "granted");

function playNotificationChime() {
    if (!soundEnabled) return;
    try {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (!AudioContext) return;
        const ctx = new AudioContext();
        if (ctx.state === "suspended") {
            ctx.resume();
        }
        const now = ctx.currentTime;

        const osc1 = ctx.createOscillator();
        const gain1 = ctx.createGain();
        osc1.type = "sine";
        osc1.frequency.setValueAtTime(587.33, now);
        gain1.gain.setValueAtTime(0.18, now);
        gain1.gain.exponentialRampToValueAtTime(0.001, now + 0.22);
        osc1.connect(gain1);
        gain1.connect(ctx.destination);
        osc1.start(now);
        osc1.stop(now + 0.22);

        const osc2 = ctx.createOscillator();
        const gain2 = ctx.createGain();
        osc2.type = "sine";
        osc2.frequency.setValueAtTime(880.00, now + 0.12);
        gain2.gain.setValueAtTime(0.22, now + 0.12);
        gain2.gain.exponentialRampToValueAtTime(0.001, now + 0.45);
        osc2.connect(gain2);
        gain2.connect(ctx.destination);
        osc2.start(now + 0.12);
        osc2.stop(now + 0.45);
    } catch (e) {
        console.warn("Audio chime error:", e);
    }
}

function sendDesktopNotification(title, body) {
    if (!desktopNotifyEnabled) return;
    if (typeof Notification !== "undefined" && Notification.permission === "granted") {
        try {
            new Notification(title, {
                body: body,
                icon: "/favicon.ico"
            });
        } catch (e) {
            console.warn("Desktop notification error:", e);
        }
    }
}

function toggleSound() {
    soundEnabled = !soundEnabled;
    const btn = document.getElementById("soundToggleBtn");
    if (btn) {
        btn.textContent = soundEnabled ? "Sound: ON" : "Sound: OFF";
        btn.classList.toggle("active", soundEnabled);
    }
    showToast(soundEnabled ? "Audio notification chime enabled" : "Audio chime muted", "info");
    if (soundEnabled) {
        playNotificationChime();
    }
}

async function requestDesktopNotification() {
    if (typeof Notification === "undefined") {
        showToast("Desktop notifications are not supported by this browser.", "error");
        return;
    }
    try {
        const permission = await Notification.requestPermission();
        const btn = document.getElementById("notifyToggleBtn");
        if (permission === "granted") {
            desktopNotifyEnabled = true;
            if (btn) {
                btn.classList.add("active");
                btn.textContent = "Notifications: ON";
            }
            showToast("Desktop notifications enabled", "success");
            sendDesktopNotification("WorkNest Admin", "Notifications are active.");
        } else {
            desktopNotifyEnabled = false;
            if (btn) {
                btn.classList.remove("active");
                btn.textContent = "Notifications: OFF";
            }
            showToast("Notifications blocked or dismissed in browser settings.", "info");
        }
    } catch (e) {
        console.error(e);
    }
}

async function logout() {
    try {
        await fetch("/dashboard/logout", { method: "POST" });
    } catch (e) {}
    window.location.href = "/dashboard/login";
}

async function loadDashboardStats() {
    try {
        const res = await fetch("/api/dashboard/stats");
        if (!res.ok) return;
        const stats = await res.json();
        const elActive = document.getElementById("kpiActiveConversations");
        const elPending = document.getElementById("kpiPendingBookings");
        const elConfirmed = document.getElementById("kpiConfirmedWeek");
        const elOpen = document.getElementById("kpiOpenComplaints");

        if (elActive) elActive.textContent = stats.active_conversations ?? 0;
        if (elPending) elPending.textContent = stats.pending_bookings ?? 0;
        if (elConfirmed) elConfirmed.textContent = stats.confirmed_week ?? 0;
        if (elOpen) elOpen.textContent = stats.open_complaints ?? 0;
    } catch (e) {
        console.error("Failed to load dashboard stats:", e);
    }
}

function downloadCsvBlob(filename, csvContent) {
    const blob = new Blob(["\uFEFF" + csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }, 100);
    showToast(`Downloaded ${filename}`);
}

function exportCurrentViewCsv() {
    const dateStr = new Date().toISOString().slice(0, 10);
    if (currentView === "bookings") {
        if (!bookings.length) {
            showToast("No bookings to export.", "info");
            return;
        }
        const headers = ["Booking ID", "Workspace Type", "Customer Name", "Customer Phone", "Seats", "Status", "Created At"];
        const rows = bookings.map(b => [
            b.booking_id || "",
            b.workspace_type || "",
            b.customer_name || "",
            b.customer_phone || "",
            b.seats || "1",
            b.status || "pending",
            b.created_at || ""
        ]);
        const csv = [headers, ...rows]
            .map(row => row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(","))
            .join("\r\n");
        downloadCsvBlob(`worknest_bookings_${dateStr}.csv`, csv);
    } else if (currentView === "complaints") {
        if (!complaints.length) {
            showToast("No complaints to export.", "info");
            return;
        }
        const headers = ["Complaint ID", "Category", "Customer Phone", "Complaint Details", "Status", "Is Follow Up", "Previous ID", "Created At"];
        const rows = complaints.map(c => [
            c.complaint_id || "",
            c.category || "",
            c.phone_number || "",
            c.complaint || "",
            c.status || "open",
            c.is_followup ? "Yes" : "No",
            c.previous_complaint_id || "",
            c.created_at || ""
        ]);
        const csv = [headers, ...rows]
            .map(row => row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(","))
            .join("\r\n");
        downloadCsvBlob(`worknest_complaints_${dateStr}.csv`, csv);
    }
}

function escapeHtml(value) {

    if (
        value === null
        || value === undefined
    ) {
        return "";
    }

    return String(value)
        .replace(
            /&/g,
            "&amp;"
        )
        .replace(
            /</g,
            "&lt;"
        )
        .replace(
            />/g,
            "&gt;"
        )
        .replace(
            /"/g,
            "&quot;"
        )
        .replace(
            /'/g,
            "&#039;"
        );
}


function getInitials(
    name,
    phone
) {

    const value =
        name
        || phone
        || "?";

    return value
        .trim()
        .substring(
            0,
            2
        )
        .toUpperCase();
}


function formatDate(value) {

    if (!value) {
        return "";
    }

    const date =
        new Date(value);

    if (
        Number.isNaN(
            date.getTime()
        )
    ) {
        return "";
    }

    return date.toLocaleDateString(
        undefined,
        {
            day: "2-digit",
            month: "short"
        }
    );
}


function formatTime(value) {

    if (!value) {
        return "";
    }

    const date =
        new Date(value);

    if (
        Number.isNaN(
            date.getTime()
        )
    ) {
        return "";
    }

    return date.toLocaleTimeString(
        undefined,
        {
            hour: "2-digit",
            minute: "2-digit"
        }
    );
}


/* =========================================================
   View Switching
   ========================================================= */

function showMessages() {

    currentView = "messages";

    selectedConversation = null;

    const newChatBtn = document.getElementById("newChatBtn");
    if (newChatBtn) newChatBtn.style.display = "inline-flex";

    const sidebarBroadcastBtn = document.getElementById("sidebarBroadcastBtn");
    if (sidebarBroadcastBtn) sidebarBroadcastBtn.style.display = "inline-flex";

    const exportBtn = document.getElementById("exportCsvBtn");
    if (exportBtn) exportBtn.style.display = "none";

    document
        .getElementById("messagesTab")
        .classList.add("active");

    const bookingsTab = document.getElementById("bookingsTab");
    if (bookingsTab) bookingsTab.classList.remove("active");

    document
        .getElementById("complaintsTab")
        .classList.remove("active");

    document
        .getElementById("listTitle")
        .textContent =
        "Conversations";

    document
        .getElementById("search")
        .placeholder =
        "Search conversations...";

    document
        .getElementById("chat")
        .classList.remove(
            "mobile-visible"
        );

    document
        .getElementById("sidebar")
        .classList.remove(
            "hidden"
        );

    renderConversations();

    showEmptyState();
}


function showBookings() {

    currentView = "bookings";

    selectedConversation = null;

    clearBookingAlert();

    const newChatBtn = document.getElementById("newChatBtn");
    if (newChatBtn) newChatBtn.style.display = "none";

    const sidebarBroadcastBtn = document.getElementById("sidebarBroadcastBtn");
    if (sidebarBroadcastBtn) sidebarBroadcastBtn.style.display = "none";

    const exportBtn = document.getElementById("exportCsvBtn");
    if (exportBtn) {
        exportBtn.style.display = "inline-flex";
        exportBtn.textContent = "Export Bookings CSV";
    }

    document
        .getElementById("messagesTab")
        .classList.remove("active");

    const bookingsTab = document.getElementById("bookingsTab");
    if (bookingsTab) bookingsTab.classList.add("active");

    document
        .getElementById("complaintsTab")
        .classList.remove("active");

    document
        .getElementById("listTitle")
        .textContent =
        "Bookings";

    document
        .getElementById("search")
        .placeholder =
        "Search bookings...";

    document
        .getElementById("chat")
        .classList.remove(
            "mobile-visible"
        );

    document
        .getElementById("sidebar")
        .classList.remove(
            "hidden"
        );

    renderBookings();

    showEmptyState();
}


function showComplaints() {

    currentView = "complaints";

    selectedConversation = null;

    clearComplaintAlert();

    const newChatBtn = document.getElementById("newChatBtn");
    if (newChatBtn) newChatBtn.style.display = "none";

    const sidebarBroadcastBtn = document.getElementById("sidebarBroadcastBtn");
    if (sidebarBroadcastBtn) sidebarBroadcastBtn.style.display = "none";

    const exportBtn = document.getElementById("exportCsvBtn");
    if (exportBtn) {
        exportBtn.style.display = "inline-flex";
        exportBtn.textContent = "Export Complaints CSV";
    }

    document
        .getElementById("messagesTab")
        .classList.remove("active");

    const bookingsTab = document.getElementById("bookingsTab");
    if (bookingsTab) bookingsTab.classList.remove("active");

    document
        .getElementById("complaintsTab")
        .classList.add("active");

    document
        .getElementById("listTitle")
        .textContent =
        "Complaints";

    document
        .getElementById("search")
        .placeholder =
        "Search complaints...";

    document
        .getElementById("chat")
        .classList.remove(
            "mobile-visible"
        );

    document
        .getElementById("sidebar")
        .classList.remove(
            "hidden"
        );

    renderComplaints();

    showEmptyState();
}


function showBookingAlert(newCount) {
    const alert = document.getElementById("bookingAlert");
    if (alert) {
        alert.textContent = newCount;
        alert.style.display = "inline-block";
    }
}


function clearBookingAlert() {
    const alert = document.getElementById("bookingAlert");
    if (alert) {
        alert.textContent = "";
        alert.style.display = "none";
    }
}



function showComplaintAlert(newComplaintCount) {

    const alert =
        document.getElementById("complaintAlert");

    alert.textContent = newComplaintCount;
    alert.style.display = "inline-block";

    const notification =
        document.getElementById("complaintNotification");

    notification.textContent =
        newComplaintCount === 1
        ? "New complaint received"
        : `${newComplaintCount} new complaints received`;
    notification.style.display = "block";
}


function clearComplaintAlert() {

    const alert =
        document.getElementById("complaintAlert");

    alert.textContent = "";
    alert.style.display = "none";

    const notification =
        document.getElementById("complaintNotification");

    notification.textContent = "";
    notification.style.display = "none";
}


function showEmptyState() {

    const chat =
        document.getElementById(
            "chat"
        );

    chat.innerHTML = `

        <div
            class="empty"
            id="emptyState"
        >

            <div class="empty-box">

                <div class="empty-title">
                    WorkNest Admin
                </div>

                <div class="empty-text">
                    Select an item from the left
                    to view its details.
                </div>

            </div>

        </div>

    `;
}


/* =========================================================
   Conversations
   ========================================================= */

async function loadConversations() {

    try {

        const response =
            await fetch(
                "/api/conversations"
            );

        if (response.status === 401) {
            window.location.href = "/dashboard/login";
            return;
        }

        if (!response.ok) {
            throw new Error(
                "Failed to load conversations"
            );
        }

        conversations =
            await response.json();

        let hasNewMessage = false;
        let latestSenderName = "";
        conversations.forEach(conv => {
            const prevUpdated = knownConversationUpdateTimes.get(conv.id);
            if (hasLoadedConversations && prevUpdated && conv.updated_at && conv.updated_at > prevUpdated) {
                hasNewMessage = true;
                latestSenderName = conv.name || conv.phone_number || "Customer";
            }
            if (conv.updated_at) {
                knownConversationUpdateTimes.set(conv.id, conv.updated_at);
            }
        });

        if (hasLoadedConversations && hasNewMessage) {
            playNotificationChime();
            sendDesktopNotification("New WhatsApp Message", `New message received from ${latestSenderName}`);
        }
        hasLoadedConversations = true;

        if (
            currentView === "messages"
        ) {
            renderConversations();
        }

    } catch (error) {

        console.error(error);

    }
}


function renderConversations() {

    const container =
        document.getElementById(
            "list"
        );

    const search =
        document
            .getElementById("search")
            .value
            .trim()
            .toLowerCase();

    const filtered =
        conversations.filter(
            conversation => {

                const phone =
                    (
                        conversation.phone_number
                        || ""
                    ).toLowerCase();

                const name =
                    (
                        conversation.name
                        || ""
                    ).toLowerCase();

                const tags =
                    (
                        conversation.tags
                        || ""
                    ).toLowerCase();

                const notes =
                    (
                        conversation.admin_notes
                        || ""
                    ).toLowerCase();

                return (
                    phone.includes(search)
                    || name.includes(search)
                    || tags.includes(search)
                    || notes.includes(search)
                );

            }
        );

    document
        .getElementById("itemCount")
        .textContent =
        filtered.length;


    if (!filtered.length) {

        container.innerHTML = `
            <div style="
                padding:30px 20px;
                text-align:center;
                color:#73777D;
                font-size:13px;
            ">
                No conversations found.
            </div>
        `;

        return;
    }


    container.innerHTML =
        filtered.map(
            conversation => {

                const id =
                    conversation.id;

                const name =
                    conversation.name
                    || conversation.phone_number
                    || "Unknown";

                const phone =
                    conversation.phone_number
                    || "";

                const active =
                    selectedConversation === id
                    ? "active"
                    : "";

                const tagsStr = conversation.tags || "";
                const tagsArr = tagsStr.split(",").map(t => t.trim()).filter(Boolean);
                const tagsHtml = tagsArr.map(t => {
                    const slug = t.toLowerCase().replace(/\s+/g, "-");
                    return `<span class="tag-badge tag-${slug}">${escapeHtml(t)}</span>`;
                }).join("");

                return `

                    <div
                        class="conversation ${active}"
                        onclick="selectConversation(${id})"
                    >

                        <div class="avatar">
                            ${escapeHtml(
                                getInitials(
                                    name,
                                    phone
                                )
                            )}
                        </div>

                        <div
                            class="conversation-info"
                        >

                            <div
                                class="conversation-top"
                            >

                                <div
                                    class="conversation-name"
                                >
                                    ${escapeHtml(
                                        name
                                    )}
                                </div>

                                <div
                                    class="conversation-date"
                                >
                                    ${formatDate(
                                        conversation.updated_at
                                    )}
                                </div>

                            </div>

                            <div
                                class="conversation-phone"
                            >
                                ${escapeHtml(
                                    phone
                                )}
                            </div>

                            ${tagsHtml ? `<div style="margin-top: 3px;">${tagsHtml}</div>` : ""}

                        </div>

                    </div>

                `;

            }
        ).join("");
}


async function selectConversation(
    id
) {

    selectedConversation = id;

    renderConversations();

    const conversation =
        conversations.find(
            item =>
                item.id === id
        );

    if (!conversation) {
        return;
    }

    document
        .getElementById("sidebar")
        .classList.add("hidden");

    document
        .getElementById("chat")
        .classList.add(
            "mobile-visible"
        );

    await loadMessages(
        id,
        conversation
    );
}


async function loadMessages(
    conversationId,
    conversation
) {

    try {

        const response =
            await fetch(
                `/api/conversations/${conversationId}/messages`
            );

        if (!response.ok) {
            throw new Error(
                "Failed to load messages"
            );
        }

        const messages =
            await response.json();

        const name =
            conversation.name
            || conversation.phone_number
            || "Unknown";

        const phone =
            conversation.phone_number
            || "";

        const tagsStr = conversation.tags || "";
        const tagsArr = tagsStr.split(",").map(t => t.trim()).filter(Boolean);
        const hasHotLead = tagsArr.includes("Hot Lead");
        const hasCorporate = tagsArr.includes("Corporate");
        const hasDayPass = tagsArr.includes("Day Pass");
        const hasActiveMember = tagsArr.includes("Active Member");
        const adminNotes = conversation.admin_notes || "";

        const chat =
            document.getElementById(
                "chat"
            );

        chat.innerHTML = `

            <div class="chat-header">

                <div class="chat-avatar">
                    ${escapeHtml(
                        getInitials(
                            name,
                            phone
                        )
                    )}
                </div>

                <div class="chat-info">

                    <div class="chat-name">
                        ${escapeHtml(name)}
                    </div>

                    <div class="chat-phone">
                        ${escapeHtml(phone)}
                    </div>

                </div>

            </div>

            <div class="contact-meta-box" id="contactMetaBox_${conversationId}">
                <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
                    <span style="font-size: 11px; font-weight: 700; color: var(--muted); text-transform: uppercase; margin-right: 4px;">Tags:</span>
                    <span class="tag-chip ${hasHotLead ? 'selected-hot-lead' : ''}" onclick="toggleContactTag(${conversationId}, 'Hot Lead')">Hot Lead</span>
                    <span class="tag-chip ${hasCorporate ? 'selected-corporate' : ''}" onclick="toggleContactTag(${conversationId}, 'Corporate')">Corporate</span>
                    <span class="tag-chip ${hasDayPass ? 'selected-day-pass' : ''}" onclick="toggleContactTag(${conversationId}, 'Day Pass')">Day Pass</span>
                    <span class="tag-chip ${hasActiveMember ? 'selected-active-member' : ''}" onclick="toggleContactTag(${conversationId}, 'Active Member')">Active Member</span>
                </div>
                <div style="display: flex; align-items: center; gap: 8px; flex: 1; min-width: 260px; max-width: 520px;">
                    <input
                        id="contactNotesInput_${conversationId}"
                        class="search"
                        style="height: 32px; font-size: 12px; margin-bottom: 0;"
                        placeholder="Internal admin notes (e.g. Wants 5 dedicated desks)..."
                        value="${escapeHtml(adminNotes)}"
                        onkeydown="if(event.key==='Enter') saveContactNotes(${conversationId})"
                    >
                    <button
                        type="button"
                        class="status-btn"
                        style="padding: 6px 14px; font-size: 11px; height: 32px; background: var(--orange); color: white;"
                        onclick="saveContactNotes(${conversationId})"
                    >
                        Save Note
                    </button>
                </div>
            </div>

            <div
                class="messages"
                id="messages"
            ></div>

            <div class="chat-composer">
                <div class="chat-composer-row">
                    <textarea
                        id="manualMessageInput"
                        class="chat-composer-textarea"
                        placeholder="Type your message to send via WhatsApp..."
                        rows="1"
                        onkeydown="handleManualKey(event, ${conversationId})"
                    ></textarea>
                    <button
                        id="manualSendBtn"
                        class="chat-composer-btn"
                        onclick="sendManualMessage(${conversationId})"
                    >
                        Send
                    </button>
                </div>
                <div class="chat-composer-footer">
                    <span>Press Enter to send (Shift + Enter for new line)</span>
                    <span id="manualSendStatus" style="font-weight: 600;"></span>
                </div>
            </div>

        `;

        const textarea = document.getElementById("manualMessageInput");
        if (textarea) {
            textarea.addEventListener("input", function() {
                this.style.height = "auto";
                this.style.height = Math.min(this.scrollHeight, 120) + "px";
            });
        }

        const container =
            document.getElementById(
                "messages"
            );


        if (!messages.length) {

            container.innerHTML = `
                <div class="empty">
                    <div class="empty-box">
                        <div class="empty-title">
                            No messages
                        </div>
                        <div class="empty-text">
                            No messages have been
                            recorded for this
                            conversation.
                        </div>
                    </div>
                </div>
            `;

            return;
        }


        container.innerHTML =
            messages.map(
                message => {

                    const outgoing =
                        message.direction
                        === "outgoing";

                    const rowClass =
                        outgoing
                        ? "outgoing"
                        : "incoming";

                    const bubbleClass =
                        outgoing
                        ? "outgoing"
                        : "incoming";

                    return `

                        <div
                            class="message-row ${rowClass}"
                        >

                            <div
                                class="message ${bubbleClass}"
                            >

                                <div>
                                    ${escapeHtml(
                                        message.message_text
                                    ).replace(
                                        /\n/g,
                                        "<br>"
                                    )}
                                </div>

                                <div
                                    class="message-time"
                                >
                                    ${formatTime(
                                        message.created_at
                                    )}
                                </div>

                            </div>

                        </div>

                    `;

                }
            ).join("");


        container.scrollTop =
            container.scrollHeight;

    } catch (error) {

        console.error(error);

    }
}


async function toggleContactTag(conversationId, tag) {
    const conv = conversations.find(c => c.id === conversationId);
    if (!conv) return;

    let currentTags = (conv.tags || "").split(",").map(t => t.trim()).filter(Boolean);
    const index = currentTags.indexOf(tag);
    if (index >= 0) {
        currentTags.splice(index, 1);
    } else {
        currentTags.push(tag);
    }

    const updatedTags = currentTags.join(", ");
    try {
        const res = await fetch(`/api/contacts/${conversationId}/meta`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tags: updatedTags })
        });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.error || "Failed to update tag");
        }
        conv.tags = updatedTags;
        showToast(`Tag ${index >= 0 ? "removed" : "added"}: ${tag}`);
        renderConversations();
        await loadMessages(conversationId, conv);
    } catch (err) {
        console.error(err);
        showToast(`Error: ${err.message}`, "error");
    }
}

async function saveContactNotes(conversationId) {
    const conv = conversations.find(c => c.id === conversationId);
    const input = document.getElementById(`contactNotesInput_${conversationId}`);
    if (!input || !conv) return;

    const note = input.value.trim();
    try {
        const res = await fetch(`/api/contacts/${conversationId}/meta`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ admin_notes: note })
        });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.error || "Failed to save note");
        }
        conv.admin_notes = note;
        showToast("Internal admin note saved", "success");
    } catch (err) {
        console.error(err);
        showToast(`Error: ${err.message}`, "error");
    }
}


/* =========================================================
   Manual Messaging & Actions
   ========================================================= */

async function sendManualMessage(conversationId) {
    const textarea = document.getElementById("manualMessageInput");
    const sendBtn = document.getElementById("manualSendBtn");
    const statusSpan = document.getElementById("manualSendStatus");
    if (!textarea || !sendBtn) return;

    const text = textarea.value.trim();
    if (!text) {
        textarea.focus();
        return;
    }

    textarea.disabled = true;
    sendBtn.disabled = true;
    sendBtn.textContent = "Sending...";
    if (statusSpan) {
        statusSpan.style.color = "var(--muted)";
        statusSpan.textContent = "Sending via WhatsApp...";
    }

    try {
        const response = await fetch(`/api/conversations/${conversationId}/messages`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: text })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Failed to send message");
        }

        textarea.value = "";
        textarea.style.height = "auto";
        textarea.disabled = false;
        sendBtn.disabled = false;
        sendBtn.textContent = "Send";
        textarea.focus();

        const methodBadge = data.method === "template" ? " (via Template)" : "";
        if (statusSpan) {
            statusSpan.style.color = "var(--green)";
            statusSpan.textContent = `Sent successfully${methodBadge}`;
            setTimeout(() => {
                if (statusSpan) statusSpan.textContent = "";
            }, 3500);
        }

        showToast(`Message sent successfully${methodBadge}`);

        const container = document.getElementById("messages");
        if (container) {
            const emptyEl = container.querySelector(".empty");
            if (emptyEl) emptyEl.remove();

            const timeStr = formatTime(new Date().toISOString());
            const bubble = document.createElement("div");
            bubble.className = "message-row outgoing";
            bubble.innerHTML = `
                <div class="message outgoing">
                    <div>${escapeHtml(text).replace(/\n/g, "<br>")}</div>
                    <div class="message-time">${escapeHtml(timeStr)}</div>
                </div>
            `;
            container.appendChild(bubble);
            container.scrollTop = container.scrollHeight;
        }

        loadConversations();

    } catch (err) {
        console.error(err);
        textarea.disabled = false;
        sendBtn.disabled = false;
        sendBtn.textContent = "Send";
        if (statusSpan) {
            statusSpan.style.color = "#DC3545";
            statusSpan.textContent = err.message;
        }
        showToast(err.message, "error");
    }
}

function handleManualKey(event, conversationId) {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendManualMessage(conversationId);
    }
}

function openNewChatModal() {
    const modal = document.getElementById("newChatModal");
    if (!modal) return;
    document.getElementById("newChatPhone").value = "";
    document.getElementById("newChatName").value = "";
    document.getElementById("newChatMessage").value = "";
    const status = document.getElementById("newChatStatus");
    if (status) status.textContent = "";
    modal.style.display = "flex";
    document.getElementById("newChatPhone").focus();
}

function closeNewChatModal() {
    const modal = document.getElementById("newChatModal");
    if (modal) modal.style.display = "none";
}

async function submitNewChat() {
    const phoneInput = document.getElementById("newChatPhone");
    const nameInput = document.getElementById("newChatName");
    const msgInput = document.getElementById("newChatMessage");
    const submitBtn = document.getElementById("newChatSubmitBtn");
    const status = document.getElementById("newChatStatus");

    const phone = (phoneInput.value || "").trim();
    const name = (nameInput.value || "").trim();
    const message = (msgInput.value || "").trim();

    if (!phone) {
        if (status) {
            status.style.color = "#DC3545";
            status.textContent = "Please enter a valid phone number.";
        }
        phoneInput.focus();
        return;
    }

    if (!message) {
        if (status) {
            status.style.color = "#DC3545";
            status.textContent = "Please enter a message to send.";
        }
        msgInput.focus();
        return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = "Sending...";
    if (status) {
        status.style.color = "var(--muted)";
        status.textContent = "Sending WhatsApp message...";
    }

    try {
        const convRes = await fetch("/api/conversations/start-or-get", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ phone_number: phone, name: name })
        });
        const convData = await convRes.json();
        if (!convRes.ok) {
            throw new Error(convData.error || "Failed to start conversation");
        }

        const convId = convData.conversation_id;

        const msgRes = await fetch(`/api/conversations/${convId}/messages`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: message })
        });
        const msgData = await msgRes.json();
        if (!msgRes.ok) {
            throw new Error(msgData.error || "Failed to send message");
        }

        closeNewChatModal();
        showToast("WhatsApp message sent successfully");

        await loadConversations();
        showMessages();
        selectConversation(convId);

    } catch (err) {
        console.error(err);
        submitBtn.disabled = false;
        submitBtn.textContent = "Send Message";
        if (status) {
            status.style.color = "#DC3545";
            status.textContent = err.message;
        }
        showToast(err.message, "error");
    }
}

function openBroadcastModal() {
    const modal = document.getElementById("broadcastModal");
    if (!modal) return;
    document.getElementById("broadcastAudience").value = "all";
    document.getElementById("broadcastMessage").value = "";
    document.getElementById("broadcastConfirmCheck").checked = false;
    const status = document.getElementById("broadcastStatus");
    if (status) status.textContent = "";
    updateBroadcastCharCount();
    updateBroadcastRecipientCount();
    modal.style.display = "flex";
    document.getElementById("broadcastMessage").focus();
}

function closeBroadcastModal() {
    const modal = document.getElementById("broadcastModal");
    if (modal) modal.style.display = "none";
}

function updateBroadcastRecipientCount() {
    const audience = document.getElementById("broadcastAudience").value;
    const countEl = document.getElementById("broadcastRecipientCount");
    if (!countEl) return;

    let targetCount = 0;
    if (audience === "all") {
        targetCount = conversations.length;
    } else {
        const lowerAudience = audience.toLowerCase();
        targetCount = conversations.filter(c => (c.tags || "").toLowerCase().includes(lowerAudience)).length;
    }

    countEl.textContent = `${targetCount} recipient${targetCount === 1 ? "" : "s"}`;
}

function updateBroadcastCharCount() {
    const textarea = document.getElementById("broadcastMessage");
    const charEl = document.getElementById("broadcastCharCount");
    if (!textarea || !charEl) return;
    charEl.textContent = `${textarea.value.length} characters`;
}

function fillBroadcastTemplate(templateKey) {
    const textarea = document.getElementById("broadcastMessage");
    if (!textarea) return;

    const templates = {
        eid: "WorkNest wishes you and your family a very blessed and peaceful Eid Mubarak! May this joyful occasion bring prosperity and happiness. We look forward to seeing you at WorkNest soon.",
        weekend: "WorkNest Weekend Pass Offer: Book your dedicated shared desk this weekend and receive a 20% discount on day passes. Fiber internet, power backup, and fresh coffee included. Reply to reserve.",
        amenities: "Exciting update from WorkNest! We have completed major upgrades to our workspace, including enhanced power backup, quiet private calling pods, and upgraded meeting room displays. Visit us today!",
        hours: "WorkNest Holiday Schedule: Front reception will be operating on adjusted holiday hours this week. 24/7 access remains active for all registered members. Contact us here for any queries."
    };

    if (templates[templateKey]) {
        textarea.value = templates[templateKey];
        updateBroadcastCharCount();
        textarea.focus();
    }
}

async function submitBroadcast() {
    const textarea = document.getElementById("broadcastMessage");
    const audienceSelect = document.getElementById("broadcastAudience");
    const confirmCheck = document.getElementById("broadcastConfirmCheck");
    const submitBtn = document.getElementById("broadcastSubmitBtn");
    const status = document.getElementById("broadcastStatus");

    const message = (textarea.value || "").trim();
    const audience = audienceSelect.value;

    if (!message) {
        if (status) {
            status.style.color = "#DC3545";
            status.textContent = "Please enter a message to broadcast.";
        }
        textarea.focus();
        return;
    }

    if (!confirmCheck.checked) {
        if (status) {
            status.style.color = "#DC3545";
            status.textContent = "Please check the confirmation box before sending.";
        }
        confirmCheck.focus();
        return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = "Broadcasting...";
    if (status) {
        status.style.color = "var(--muted)";
        status.textContent = "Delivering broadcast to selected contacts...";
    }

    try {
        const response = await fetch("/api/broadcast", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: message, tag: audience })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Failed to send broadcast");
        }

        closeBroadcastModal();
        const successNotice = `Broadcast completed: ${data.sent} sent, ${data.failed} failed out of ${data.total} contacts.`;
        showToast(successNotice, data.failed > 0 ? "info" : "success");

        await loadConversations();
        await loadDashboardStats();

    } catch (err) {
        console.error(err);
        submitBtn.disabled = false;
        submitBtn.textContent = "Send Broadcast";
        if (status) {
            status.style.color = "#DC3545";
            status.textContent = err.message;
        }
        showToast(err.message, "error");
    }
}

async function chatWithCustomer(phoneNumber, customerName) {
    if (!phoneNumber) {
        showToast("No phone number available for this booking", "error");
        return;
    }

    showToast("Opening conversation with customer...");

    try {
        const response = await fetch("/api/conversations/start-or-get", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ phone_number: phoneNumber, name: customerName })
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || "Could not open conversation");
        }

        await loadConversations();
        showMessages();
        await selectConversation(data.conversation_id);

        setTimeout(() => {
            const input = document.getElementById("manualMessageInput");
            if (input) input.focus();
        }, 150);

    } catch (err) {
        console.error(err);
        showToast(err.message, "error");
    }
}


/* =========================================================
   Complaints
   ========================================================= */

async function loadComplaints() {

    try {

        const response =
            await fetch(
                "/api/complaints"
            );

        if (response.status === 401) {
            window.location.href = "/dashboard/login";
            return;
        }

        if (!response.ok) {
            throw new Error(
                "Failed to load complaints"
            );
        }

        complaints =
            await response.json();

        const complaintIds =
            new Set(
                complaints.map(
                    complaint => complaint.complaint_id
                )
            );

        if (hasLoadedComplaints) {
            const newComplaintCount =
                complaints.filter(
                    complaint =>
                        !knownComplaintIds.has(
                            complaint.complaint_id
                        )
                ).length;

            if (newComplaintCount > 0) {
                showComplaintAlert(newComplaintCount);
                playNotificationChime();
                sendDesktopNotification("New Complaint Received", `${newComplaintCount} new customer complaint(s) received.`);
            }
        }

        knownComplaintIds = complaintIds;
        hasLoadedComplaints = true;

        if (
            currentView === "complaints"
        ) {
            renderComplaints();
        }

    } catch (error) {

        console.error(error);

    }
}


function renderComplaints() {

    const container =
        document.getElementById(
            "list"
        );

    const search =
        document
            .getElementById("search")
            .value
            .trim()
            .toLowerCase();

    const filtered =
        complaints.filter(
            complaint => {

                const id =
                    (
                        complaint.complaint_id
                        || ""
                    ).toLowerCase();

                const text =
                    (
                        complaint.complaint
                        || ""
                    ).toLowerCase();

                const category =
                    (
                        complaint.category
                        || ""
                    ).toLowerCase();

                const phone =
                    (
                        complaint.phone_number
                        || ""
                    ).toLowerCase();

                return (
                    id.includes(search)
                    || text.includes(search)
                    || category.includes(search)
                    || phone.includes(search)
                );

            }
        );


    document
        .getElementById("itemCount")
        .textContent =
        filtered.length;


    if (!filtered.length) {

        container.innerHTML = `
            <div style="
                padding:30px 20px;
                text-align:center;
                color:#73777D;
                font-size:13px;
            ">
                No complaints found.
            </div>
        `;

        return;
    }


    container.innerHTML =
        filtered.map(
            complaint => {

                const status =
                    complaint.status
                    || "open";

                return `

                    <div
                        class="complaint"
                    >

                        <div
                            class="complaint-id"
                        >
                            ${escapeHtml(
                                complaint.complaint_id
                            )}
                        </div>

                        <div
                            class="complaint-category"
                        >
                            ${escapeHtml(
                                complaint.category
                                || "Other"
                            )}
                        </div>

                        ${complaint.is_followup
                            ? `
                                <div class="followup-badge">
                                    Follow-up complaint
                                </div>

                                <div
                                    style="
                                        margin-top:5px;
                                        color:#73777D;
                                        font-size:11px;
                                    "
                                >
                                    Previous: ${escapeHtml(
                                        complaint.previous_complaint_id
                                        || "Unknown"
                                    )}
                                </div>
                            `
                            : ""}

                        <div
                            class="complaint-text"
                        >
                            ${escapeHtml(
                                complaint.complaint
                            )}
                        </div>

                        <div>
                            <span
                                class="status status-${escapeHtml(status)}"
                            >
                                ${escapeHtml(
                                    status.replace(
                                        "_",
                                        " "
                                    )
                                )}
                            </span>
                        </div>

                        <select
                            class="complaint-select"
                            onchange="updateComplaintStatus(
                                '${escapeHtml(
                                    complaint.complaint_id
                                )}',
                                this.value
                            )"
                        >

                            <option
                                value="open"
                                ${status === "open"
                                    ? "selected"
                                    : ""}
                            >
                                Open
                            </option>

                            <option
                                value="in_progress"
                                ${status === "in_progress"
                                    ? "selected"
                                    : ""}
                            >
                                In Progress
                            </option>

                            <option
                                value="resolved"
                                ${status === "resolved"
                                    ? "selected"
                                    : ""}
                            >
                                Resolved
                            </option>

                            <option
                                value="closed"
                                ${status === "closed"
                                    ? "selected"
                                    : ""}
                            >
                                Closed
                            </option>

                        </select>

                        <div
                            style="
                                margin-top:7px;
                                color:#73777D;
                                font-size:11px;
                            "
                        >
                            ${escapeHtml(
                                complaint.phone_number
                                || ""
                            )}
                            -
                            ${formatDate(
                                complaint.created_at
                            )}
                        </div>

                    </div>

                `;

            }
        ).join("");
}


async function updateComplaintStatus(
    complaintId,
    status
) {

    try {

        const response =
            await fetch(
                `/api/complaints/${encodeURIComponent(
                    complaintId
                )}/status`,
                {
                    method: "PATCH",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body: JSON.stringify({
                        status: status
                    })
                }
            );

        if (!response.ok) {

            throw new Error(
                "Failed to update complaint"
            );

        }

        await loadComplaints();

    } catch (error) {

        console.error(error);

        alert(
            "Unable to update complaint status."
        );

    }
}


/* =========================================================
   Bookings
   ========================================================= */

async function loadBookings() {

    try {

        const response =
            await fetch(
                "/api/bookings"
            );

        if (response.status === 401) {
            window.location.href = "/dashboard/login";
            return;
        }

        if (!response.ok) {
            throw new Error(
                "Failed to load bookings"
            );
        }

        bookings =
            await response.json();

        const bookingIds =
            new Set(
                bookings.map(
                    b => b.booking_id
                )
            );

        if (hasLoadedBookings) {
            const newBookingCount =
                bookings.filter(
                    b =>
                        !knownBookingIds.has(
                            b.booking_id
                        )
                ).length;

            if (newBookingCount > 0) {
                showBookingAlert(newBookingCount);
                playNotificationChime();
                sendDesktopNotification("New Booking Received", `${newBookingCount} new workspace reservation(s) received.`);
            }
        }

        knownBookingIds = bookingIds;
        hasLoadedBookings = true;

        if (
            currentView === "bookings"
        ) {
            renderBookings();
        }

    } catch (error) {

        console.error(error);

    }
}


function renderBookings() {

    const container =
        document.getElementById(
            "list"
        );

    const search =
        document
            .getElementById("search")
            .value
            .trim()
            .toLowerCase();

    const filtered =
        bookings.filter(
            booking => {

                const id =
                    (
                        booking.booking_id
                        || ""
                    ).toLowerCase();

                const type =
                    (
                        booking.workspace_type
                        || ""
                    ).toLowerCase();

                const name =
                    (
                        booking.customer_name
                        || ""
                    ).toLowerCase();

                const phone =
                    (
                        booking.customer_phone
                        || ""
                    ).toLowerCase();

                return (
                    id.includes(search)
                    || type.includes(search)
                    || name.includes(search)
                    || phone.includes(search)
                );

            }
        );

    document
        .getElementById("itemCount")
        .textContent =
        filtered.length;

    if (!filtered.length) {

        container.innerHTML = `
            <div style="
                padding:30px 20px;
                text-align:center;
                color:#73777D;
                font-size:13px;
            ">
                No bookings found.
            </div>
        `;

        return;
    }

    container.innerHTML =
        filtered.map(
            booking => {

                const status =
                    booking.status
                    || "pending";

                return `

                    <div
                        class="booking"
                        onclick="selectBooking('${escapeHtml(booking.booking_id)}')"
                    >

                        <div
                            class="booking-id"
                        >
                            ${escapeHtml(
                                booking.booking_id
                            )}
                        </div>

                        <div
                            class="booking-workspace"
                        >
                            ${escapeHtml(
                                booking.workspace_type
                                || "Workspace"
                            )}
                        </div>

                        <div
                            class="booking-info"
                        >
                            <strong>Name:</strong> ${escapeHtml(booking.customer_name || "N/A")}<br>
                            <strong>Phone:</strong> ${escapeHtml(booking.customer_phone || "N/A")}<br>
                            <strong>Seats / Persons:</strong> ${escapeHtml(booking.seats || "1")}
                        </div>

                        <div>
                            <span
                                class="status status-${escapeHtml(status)}"
                            >
                                ${escapeHtml(
                                    status.replace(
                                        "_",
                                        " "
                                    )
                                )}
                            </span>
                        </div>

                        <select
                            class="booking-select"
                            onclick="event.stopPropagation()"
                            onchange="updateBookingStatus(
                                '${escapeHtml(
                                    booking.booking_id
                                )}',
                                this.value
                            )"
                        >

                            <option
                                value="pending"
                                ${status === "pending"
                                    ? "selected"
                                    : ""}
                            >
                                Pending
                            </option>

                            <option
                                value="confirmed"
                                ${status === "confirmed"
                                    ? "selected"
                                    : ""}
                            >
                                Confirmed
                            </option>

                            <option
                                value="completed"
                                ${status === "completed"
                                    ? "selected"
                                    : ""}
                            >
                                Completed
                            </option>

                            <option
                                value="cancelled"
                                ${status === "cancelled"
                                    ? "selected"
                                    : ""}
                            >
                                Cancelled
                            </option>

                        </select>

                        <div
                            style="
                                margin-top:7px;
                                color:#73777D;
                                font-size:11px;
                            "
                        >
                            ${formatDate(
                                booking.created_at
                            )}
                        </div>

                    </div>

                `;

            }
        ).join("");
}


function showToast(message, type = "success") {
    const container = document.getElementById("toastContainer");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;

    toast.innerHTML = `<span>${escapeHtml(message)}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = "0";
        toast.style.transform = "translateY(-12px)";
        setTimeout(() => toast.remove(), 300);
    }, 3200);
}


function selectBooking(bookingId) {

    const booking = bookings.find(b => b.booking_id === bookingId);
    if (!booking) return;

    document.getElementById("sidebar").classList.add("hidden");
    document.getElementById("chat").classList.add("mobile-visible");

    const status = booking.status || "pending";
    const chat = document.getElementById("chat");

    let targetPhone = booking.whatsapp_phone || booking.customer_phone || "";
    let cleanPhone = targetPhone.replace(/[^0-9]/g, "");
    if (cleanPhone.startsWith("00")) {
        cleanPhone = cleanPhone.slice(2);
    } else if (cleanPhone.startsWith("0") && cleanPhone.length === 11) {
        cleanPhone = "92" + cleanPhone.slice(1);
    }

    chat.innerHTML = `
        <div class="chat-header">
            <div class="chat-avatar" style="background:var(--orange-dark); font-size: 17px; font-weight: 800;">
                W
            </div>
            <div class="chat-info">
                <div class="chat-name">${escapeHtml(booking.booking_id)} - ${escapeHtml(booking.workspace_type)}</div>
                <div class="chat-phone">${escapeHtml(booking.customer_name || 'Customer')} (${escapeHtml(booking.customer_phone || '')})</div>
            </div>
        </div>
        <div style="padding: 24px; overflow-y: auto; display: flex; flex-direction: column; gap: 20px;">
            <div style="background: #ffffff; border: 1px solid var(--border); border-radius: 12px; padding: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 20px; border-bottom: 1px solid var(--border); padding-bottom: 14px;">
                    <h2 style="margin:0; font-size: 18px; color: var(--text);">Workspace Reservation Details</h2>
                    <span id="bookingCardStatus" class="status status-${escapeHtml(status)}" style="font-size: 12px; padding: 6px 14px;">
                        ${escapeHtml(status.toUpperCase())}
                    </span>
                </div>
                <div style="display: grid; grid-template-columns: 140px 1fr; row-gap: 12px; font-size: 14px;">
                    <span style="color: var(--muted); font-weight: 600;">Booking ID:</span>
                    <span><strong>${escapeHtml(booking.booking_id)}</strong></span>

                    <span style="color: var(--muted); font-weight: 600;">Workspace:</span>
                    <span><strong>${escapeHtml(booking.workspace_type)}</strong></span>

                    <span style="color: var(--muted); font-weight: 600;">Customer Name:</span>
                    <span>${escapeHtml(booking.customer_name || 'N/A')}</span>

                    <span style="color: var(--muted); font-weight: 600;">Contact Phone:</span>
                    <span>
                        ${escapeHtml(booking.customer_phone || 'N/A')}
                        ${cleanPhone ? `<a href="https://wa.me/${cleanPhone}" target="_blank" style="margin-left: 10px; color: var(--green); text-decoration: none; font-weight: 700;">Open in WhatsApp</a>` : ''}
                        ${cleanPhone ? `<button type="button" onclick="chatWithCustomer('${cleanPhone}', '${escapeHtml(booking.customer_name || '')}')" style="margin-left: 10px; background: var(--orange); color: white; border: none; border-radius: 6px; padding: 4px 10px; font-size: 11px; font-weight: 700; cursor: pointer;">Chat in Dashboard</button>` : ''}
                    </span>

                    <span style="color: var(--muted); font-weight: 600;">Seats / Persons:</span>
                    <span>${escapeHtml(booking.seats || '1')}</span>

                    <span style="color: var(--muted); font-weight: 600;">Received Date:</span>
                    <span>${formatDate(booking.created_at)} ${formatTime(booking.created_at)}</span>
                </div>

                <div style="margin-top: 24px; padding-top: 18px; border-top: 1px solid var(--border);">
                    <div style="font-size: 12px; font-weight: 700; color: var(--muted); margin-bottom: 12px; letter-spacing: 0.5px;">UPDATE RESERVATION STATUS:</div>
                    <div id="bookingActionGroup" style="display: flex; gap: 10px; flex-wrap: wrap;">
                        <button
                            class="status-btn ${status === 'confirmed' ? 'current-status' : ''}"
                            style="background: #198754; color: white;"
                            ${status === 'confirmed' ? 'disabled' : ''}
                            onclick="updateBookingStatus('${escapeHtml(booking.booking_id)}', 'confirmed', this)"
                        >
                            Mark Confirmed
                        </button>
                        <button
                            class="status-btn ${status === 'completed' ? 'current-status' : ''}"
                            style="background: #2563EB; color: white;"
                            ${status === 'completed' ? 'disabled' : ''}
                            onclick="updateBookingStatus('${escapeHtml(booking.booking_id)}', 'completed', this)"
                        >
                            Mark Completed
                        </button>
                        <button
                            class="status-btn ${status === 'pending' ? 'current-status' : ''}"
                            style="background: #FFF1D8; color: #D98208; border: 1px solid #D98208;"
                            ${status === 'pending' ? 'disabled' : ''}
                            onclick="updateBookingStatus('${escapeHtml(booking.booking_id)}', 'pending', this)"
                        >
                            Mark Pending
                        </button>
                        <button
                            class="status-btn ${status === 'cancelled' ? 'current-status' : ''}"
                            style="background: #DC3545; color: white;"
                            ${status === 'cancelled' ? 'disabled' : ''}
                            onclick="updateBookingStatus('${escapeHtml(booking.booking_id)}', 'cancelled', this)"
                        >
                            Cancel Booking
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `;
}



async function updateBookingStatus(
    bookingId,
    status,
    btnElement = null
) {

    const parentGroup = btnElement ? btnElement.parentElement : null;
    let oldBtnText = "";

    if (btnElement) {
        oldBtnText = btnElement.innerHTML;
        btnElement.innerHTML = `Updating...`;
        if (parentGroup) {
            parentGroup.querySelectorAll("button").forEach(b => b.disabled = true);
        }
    }

    try {

        const response =
            await fetch(
                `/api/bookings/${encodeURIComponent(
                    bookingId
                )}/status`,
                {
                    method: "PATCH",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body: JSON.stringify({
                        status: status
                    })
                }
            );

        if (!response.ok) {
            const err = await response.json();
            throw new Error(
                err.error || "Failed to update booking"
            );
        }

        await loadBookings();

        showToast(
            `Booking ${bookingId} updated to ${status.toUpperCase()}`,
            "success"
        );

        if (currentView === "bookings") {
            selectBooking(bookingId);
        }

    } catch (error) {

        console.error(error);

        showToast(
            `Error: ${error.message}`,
            "error"
        );

        if (btnElement) {
            btnElement.innerHTML = oldBtnText;
            if (parentGroup) {
                parentGroup.querySelectorAll("button").forEach(b => b.disabled = false);
            }
        }

    }
}


/* =========================================================
   Search
   ========================================================= */

document
    .getElementById("search")
    .addEventListener(
        "input",
        function() {

            if (
                currentView === "messages"
            ) {

                renderConversations();

            } else if (
                currentView === "bookings"
            ) {

                renderBookings();

            } else {

                renderComplaints();

            }

        }
    );


/* =========================================================
   Refresh
   ========================================================= */

async function refresh() {

    await loadDashboardStats();

    await loadConversations();

    await loadBookings();

    await loadComplaints();

    if (
        currentView === "messages"
        && selectedConversation
    ) {

        const conversation =
            conversations.find(
                item =>
                    item.id ===
                    selectedConversation
            );

        if (conversation) {

            await loadMessages(
                selectedConversation,
                conversation
            );

        }

    }

}


/* =========================================================
   Initial Load
   ========================================================= */

loadDashboardStats();

loadConversations();

loadBookings();

loadComplaints();

setInterval(
    refresh,
    5000
);

</script>

</body>

</html>
"""

    return HTMLResponse(content=html)
