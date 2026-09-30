import os
import base64
import hashlib
import hmac
import re
import secrets
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

        result = (
            supabase.table("whatsapp_conversations")
            .select(
                "id,"
                "contact_id,"
                "status,"
                "created_at,"
                "updated_at,"
                "whatsapp_contacts("
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
                    "contact_id": conversation.get("contact_id"),
                    "phone_number": contact.get("phone_number"),
                    "name": contact.get("name"),
                    "status": conversation.get("status"),
                    "created_at": conversation.get("created_at"),
                    "updated_at": conversation.get("updated_at"),
                }
            )

        return conversations

    except Exception as e:

        print("Conversation API error:", e)

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

    overflow: hidden;
}

/* =========================================================
   Sidebar
   ========================================================= */

.sidebar {
    width: var(--sidebar-width);

    background: var(--white);

    border-right:
        1px solid var(--border);

    display: flex;

    flex-direction: column;
}

.brand {
    height: 72px;

    display: flex;

    align-items: center;

    padding: 0 22px;

    border-bottom:
        1px solid var(--border);
}

.brand-logo {
    width: 40px;
    height: 40px;

    border-radius: 12px;

    background:
        var(--orange);

    display: flex;

    align-items: center;
    justify-content: center;

    color: white;

    font-size: 18px;

    font-weight: 800;
}

.brand-text {
    margin-left: 12px;
}

.brand-title {
    font-size: 17px;

    font-weight: 800;
}

.brand-subtitle {
    color: var(--muted);

    font-size: 12px;

    margin-top: 2px;
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

<div class="app">

    <aside
        class="sidebar"
        id="sidebar"
    >

        <div class="brand">

            <div class="brand-logo">
                W
            </div>

            <div class="brand-text">

                <div class="brand-title">
                    WorkNest
                </div>

                <div class="brand-subtitle">
                    WhatsApp Admin
                </div>

            </div>

        </div>


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

                <button
                    id="newChatBtn"
                    class="new-chat-btn"
                    onclick="openNewChatModal()"
                    title="Start new conversation with any phone number"
                >
                    + New Message
                </button>

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


<script>

let conversations = [];
let complaints = [];
let bookings = [];

let knownComplaintIds = new Set();
let knownBookingIds = new Set();

let hasLoadedComplaints = false;
let hasLoadedBookings = false;

let selectedConversation = null;

let currentView = "messages";


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

                return (
                    phone.includes(search)
                    || name.includes(search)
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
