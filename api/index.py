import os
import base64
import hashlib
import hmac
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

load_dotenv()

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

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
        "6. Talk to Reception\n\n"
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


def send_resolved_notification(phone_number, complaint_id):

    message = (
        "WorkNest Complaint Update\n\n"
        f"Your complaint {complaint_id} has been resolved.\n\n"
        "Does the issue persist?\n\n"
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

    if message in ["1", "2"] and supabase and contact_id:

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
                    "If you need anything else, please type MENU."
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
                "Type MENU to return to the main menu."
            )

    # ========================================================
    # MAIN MENU
    # ========================================================

    if state == "main_menu":

        # ----------------------------------------------------
        # Book Workspace
        # ----------------------------------------------------

        if message == "1":

            state_data["state"] = "booking_type"

            return (
                "Book a Workspace\n\n"
                "Please select a workspace type:\n\n"
                "1. Hot Desk\n"
                "2. Dedicated Desk\n"
                "3. Private Office\n"
                "4. Meeting Room\n"
                "5. Conference Room\n\n"
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
                "1. Daily Pass - PKR XXXX\n"
                "2. Weekly Plan - PKR XXXX\n"
                "3. Monthly Plan - PKR XXXX\n"
                "4. Dedicated Desk - PKR XXXX\n"
                "5. Private Office - PKR XXXX\n"
                "6. Meeting Room - PKR XXXX\n\n"
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
                "Type MENU to return to the main menu."
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
                "Type MENU to return to the main menu."
            )

        return "Please select a valid option.\n\n" + main_menu()

    # ========================================================
    # BOOKING TYPE
    # ========================================================

    if state == "booking_type":

        workspace_types = {
            "1": "Hot Desk",
            "2": "Dedicated Desk",
            "3": "Private Office",
            "4": "Meeting Room",
            "5": "Conference Room",
        }

        if message not in workspace_types:

            return (
                "Please select a valid workspace type:\n\n"
                "1. Hot Desk\n"
                "2. Dedicated Desk\n"
                "3. Private Office\n"
                "4. Meeting Room\n"
                "5. Conference Room"
            )

        state_data["booking"]["workspace_type"] = workspace_types[message]

        state_data["state"] = "booking_date"

        return (
            f"Selected: {workspace_types[message]}\n\n"
            "Please enter your booking date.\n\n"
            "Example: 25 September 2026"
        )

    # ========================================================
    # BOOKING DATE
    # ========================================================

    if state == "booking_date":

        state_data["booking"]["date"] = message

        state_data["state"] = "booking_time"

        return "Please enter your preferred start time.\n\n" "Example: 10:00 AM"

    # ========================================================
    # BOOKING TIME
    # ========================================================

    if state == "booking_time":

        state_data["booking"]["time"] = message

        state_data["state"] = "booking_duration"

        return (
            "Please enter the required duration.\n\n"
            "Example:\n"
            "2 hours\n"
            "or\n"
            "Full day"
        )

    # ========================================================
    # BOOKING DURATION
    # ========================================================

    if state == "booking_duration":

        state_data["booking"]["duration"] = message

        state_data["state"] = "booking_confirmation"

        booking = state_data["booking"]

        return (
            "Please confirm your booking:\n\n"
            f"Workspace: {booking['workspace_type']}\n"
            f"Date: {booking['date']}\n"
            f"Time: {booking['time']}\n"
            f"Duration: {booking['duration']}\n\n"
            "Reply with:\n"
            "1. Confirm\n"
            "2. Cancel"
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

            return (
                "Booking request received.\n\n"
                f"Booking ID: {booking_id}\n\n"
                f"Workspace: {booking['workspace_type']}\n"
                f"Date: {booking['date']}\n"
                f"Time: {booking['time']}\n"
                f"Duration: {booking['duration']}\n\n"
                "Our reception team will confirm "
                "availability and finalize your booking.\n\n"
                "Thank you for choosing WorkNest."
            )

        if message == "2":

            state_data["state"] = "main_menu"
            state_data["booking"] = {}

            return "Your booking request has been cancelled.\n\n" + main_menu()

        return "Please reply with:\n\n" "1. Confirm\n" "2. Cancel"

    # ========================================================
    # BOOKING LOOKUP
    # ========================================================

    if state == "booking_lookup":

        booking_id = message.upper()

        state_data["state"] = "main_menu"

        return (
            f"Booking ID: {booking_id}\n\n"
            "Your booking lookup request has been received.\n\n"
            "Booking lookup can be connected to the "
            "WorkNest booking database here.\n\n"
            "Type MENU to return to the main menu."
        )

    # ========================================================
    # PRICING
    # ========================================================

    if state == "pricing":

        pricing = {
            "1": "Daily Pass - PKR XXXX",
            "2": "Weekly Plan - PKR XXXX",
            "3": "Monthly Plan - PKR XXXX",
            "4": "Dedicated Desk - PKR XXXX",
            "5": "Private Office - PKR XXXX",
            "6": "Meeting Room - PKR XXXX",
        }

        if message in pricing:

            state_data["state"] = "main_menu"

            return (
                f"{pricing[message]}\n\n"
                "For exact pricing and availability, "
                "please contact reception.\n\n"
                "Type MENU to return to the main menu."
            )

        return (
            "Please select a valid pricing option:\n\n"
            "1. Daily Pass\n"
            "2. Weekly Plan\n"
            "3. Monthly Plan\n"
            "4. Dedicated Desk\n"
            "5. Private Office\n"
            "6. Meeting Room"
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
            "Type MENU to return to the main menu."
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
                "7. Other"
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
                "Type MENU to return to the main menu."
            )
        else:
            response_text = (
                "Your complaint has been submitted.\n\n"
                f"Complaint ID: {complaint_id}\n"
                f"Category: {category}\n"
                "Status: Open\n\n"
                "Our team will review your complaint and contact you "
                "if further information is required.\n\n"
                "Type MENU to return to the main menu."
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
            "Type MENU to return to the main menu."
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

    padding: 10px;

    border-radius: 8px;

    cursor: pointer;

    font-size: 13px;

    font-weight: 700;

    color: var(--muted);
}

.tab.active {
    background:
        var(--orange-light);

    color:
        var(--orange-dark);
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
                id="complaintsTab"
                onclick="showComplaints()"
            >
                Complaints
            </button>

        </div>


        <div class="sidebar-header">

            <div class="sidebar-title-row">

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

            <input
                id="search"
                class="search"
                type="text"
                placeholder="Search..."
                autocomplete="off"
            >

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

    document
        .getElementById("messagesTab")
        .classList.add("active");

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


function showComplaints() {

    currentView = "complaints";

    selectedConversation = null;

    document
        .getElementById("messagesTab")
        .classList.remove("active");

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

        `;

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
   Complaints
   ========================================================= */

async function loadComplaints() {

    try {

        const response =
            await fetch(
                "/api/complaints"
            );

        if (!response.ok) {
            throw new Error(
                "Failed to load complaints"
            );
        }

        complaints =
            await response.json();

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
                            ·
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
