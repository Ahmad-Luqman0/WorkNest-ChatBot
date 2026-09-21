# WorkNest WhatsApp Chatbot

A FastAPI-powered WhatsApp chatbot and admin dashboard for WorkNest Co-Working. It handles workspace requests, complaints, resolved-complaint follow-ups, and dashboard management through Supabase. 🚀

## Features

- WhatsApp webhook integration
- Workspace booking conversation flow
- Complaint submission and tracking
- Resolved complaint notifications
- Automatic follow-up complaint creation when an issue persists
- Follow-up flags linked to the previous complaint
- Admin dashboard for conversations and complaints
- Username and password dashboard login
- Vercel deployment configuration

## Project Structure

```text
api/index.py       FastAPI app, WhatsApp webhook, APIs, and dashboard
schema.sql         Supabase database schema and migrations
requirements.txt   Python dependencies
vercel.json        Vercel routing configuration
```

## Local Setup

Create and activate a virtual environment:

```bash
python -m venv venv
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
WHATSAPP_TOKEN=your_whatsapp_token
PHONE_NUMBER_ID=your_phone_number_id
VERIFY_TOKEN=your_webhook_verify_token
SUPABASE_URL=your_supabase_url
SUPABASE_SERVICE_ROLE_KEY=your_supabase_service_role_key
DASHBOARD_SESSION_SECRET=your_long_random_secret
```

Run the app locally:

```bash
uvicorn api.index:app --reload --port 8000
```

Open the dashboard at:

```text
http://localhost:8000/dashboard
```

## Supabase Setup

1. Open the Supabase SQL Editor.
2. Run the contents of `schema.sql`.
3. Create a dashboard user with a PBKDF2 password hash.
4. Use the username and password on `/dashboard`.

The database stores contacts, conversations, messages, complaints, follow-up links, notification status, and customer resolution responses.

## WhatsApp Webhook

For local testing, expose the app with ngrok:

```bash
ngrok http 8000
```

Set the WhatsApp webhook URL to:

```text
https://your-ngrok-domain.ngrok-free.app/webhook
```

Use the same value as `VERIFY_TOKEN` during webhook verification.

## Vercel Deployment

1. Import this repository into Vercel.
2. Add all environment variables from `.env` in the Vercel project settings.
3. Deploy the project.
4. Set the WhatsApp webhook URL to:

```text
https://your-project.vercel.app/webhook
```

Open the admin dashboard at:

```text
https://your-project.vercel.app/dashboard
```

## Important Notes

- Never commit `.env` or service-role keys.
- Keep `DASHBOARD_SESSION_SECRET` stable between deployments.
- Supabase provides persistent storage for dashboard data.
- The current chatbot conversation state uses in-memory storage; persistent multi-instance state should be moved to Supabase for high-volume production deployments.
