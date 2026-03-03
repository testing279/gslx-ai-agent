"""
GSLX AI Voice Agent
====================
Twilio se call aati hai → AI brain (Claude) baat karta hai →
Deal close hone pe Google Sheets mein save karta hai →
WhatsApp/Email notification bhejta hai
"""

from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client as TwilioClient
import anthropic
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import os
import json
import re

app = Flask(__name__)

# ─── Clients ────────────────────────────────────────────────
anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
twilio_client    = TwilioClient(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])

# ─── Google Sheets Setup ─────────────────────────────────────
def get_sheet():
    creds_json = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
    creds = Credentials.from_service_account_info(
        creds_json,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(os.environ["GOOGLE_SHEET_ID"]).sheet1

# ─── In-memory conversation store ───────────────────────────
# { call_sid: { "messages": [...], "data": {...} } }
conversations = {}

# ─── GSLX System Prompt ──────────────────────────────────────
SYSTEM_PROMPT = """You are Ankit, a friendly sales representative at Global Secure Layer X Pvt. Ltd. (GSLX).
You speak naturally in Hinglish (mix of Hindi and English) — switching to pure English if the caller speaks English.

YOUR GOAL: Understand the caller's needs, explain relevant GSLX services, and close the deal by getting their details.

COMPANY INFO:
- Name: Global Secure Layer X Private Limited (GSLX)
- Offices: Delhi (Virtual) and Buxar, Bihar (HQ)
- Email: sales@globalsecurelayerx.in
- Phone: +91-8929031413

CYBERSECURITY PLANS:
1. Silver Plan - ₹45,000/month (12-month contract)
   - Basic Web Security Audit, OWASP Top 10 Scan, 24/7 Website Monitoring
   - Best for: Startups, Small Businesses, Personal Brands

2. Gold Plan - ₹1,20,000/month (12-month contract)
   - VAPT Quarterly, API Security, Cloud Security (AWS/Azure/GCP)
   - Dark Web Monitoring, Priority Support
   - Best for: SMEs, SaaS, E-commerce

3. Platinum Plan - ₹3,00,000/month (12-month contract)
   - Full VAPT, Red Team/Blue Team, 24/7 SOC, Dedicated Consultant
   - ISO 27001, SOC 2, CERT-In Compliance
   - Best for: Enterprises, Financial Institutions, Government

WEB DEVELOPMENT PRICING (One-time):
- Business Website: ₹25,000 – ₹45,000
- Corporate/Portfolio (8-15 pages): ₹50,000 – ₹90,000
- E-Commerce Website: ₹80,000 – ₹2,00,000
- Custom Admin Panel: ₹40,000 – ₹1,20,000

CONVERSATION FLOW:
1. Greet warmly — "Namaste! GSLX mein aapka swagat hai, main Ankit bol raha hoon. Aap kaise hain?"
2. Ask their name and company
3. Understand their problem/need
4. Recommend the best plan
5. Handle objections confidently but honestly
6. Try to close — "Kya main aapko ek free security audit schedule kar sakta hoon?"
7. Get their: Name, Company, Email, Budget, Requirement
8. Confirm details and say you'll follow up

IMPORTANT RULES:
- Keep responses SHORT — max 2-3 sentences (this is a phone call!)
- Be warm, professional, never pushy
- If they ask something you don't know, say "Main aapko callback mein details dunga"
- When you have collected Name + Company + Requirement + Budget, add this EXACT JSON at the end of your response (hidden from speech):
  [LEAD_DATA:{"name":"...","company":"...","phone":"...","requirement":"...","budget":"...","language":"...","status":"hot/warm/cold"}]
"""

# ─── Routes ──────────────────────────────────────────────────

@app.route("/voice/incoming", methods=["POST"])
def incoming_call():
    """Twilio calls this when a call comes in"""
    call_sid = request.form.get("CallSid")
    caller   = request.form.get("From", "Unknown")

    # Initialize conversation
    conversations[call_sid] = {
        "messages": [],
        "caller":   caller,
        "data":     {}
    }

    resp = VoiceResponse()
    gather = Gather(
        input="speech",
        action=f"/voice/respond?call_sid={call_sid}",
        method="POST",
        language="hi-IN",           # Hindi first
        speech_timeout="auto",
        enhanced=True
    )
    gather.say(
        "Namaste! Global Secure Layer X mein aapka swagat hai. "
        "Main Ankit hoon. Aap kaise hain? Aapki kya help kar sakta hoon?",
        voice="Polly.Aditi",        # Indian Hindi voice
        language="hi-IN"
    )
    resp.append(gather)

    # If no input
    resp.say("Koi awaaz nahi aayi. Kripya dobara call karein.", language="hi-IN")
    return Response(str(resp), mimetype="text/xml")


@app.route("/voice/respond", methods=["POST"])
def respond():
    """Process caller's speech and respond with AI"""
    call_sid      = request.args.get("call_sid")
    speech_result = request.form.get("SpeechResult", "")
    language      = request.form.get("Language", "hi-IN")

    if not speech_result:
        return _re_gather(call_sid, "Sorry, main sun nahi paya. Kya aap dobara bol sakte hain?")

    conv = conversations.get(call_sid, {"messages": [], "caller": "Unknown", "data": {}})

    # Add user message
    conv["messages"].append({"role": "user", "content": speech_result})

    # Detect language
    is_english = language.startswith("en") or _is_english(speech_result)

    # Get AI response
    ai_response = _get_ai_response(conv["messages"], is_english)

    # Check if lead data was collected
    lead_data = _extract_lead_data(ai_response)
    clean_response = _clean_response(ai_response)

    if lead_data:
        lead_data["phone"]     = conv["caller"]
        lead_data["call_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conv["data"].update(lead_data)
        _save_to_sheets(lead_data)
        _send_whatsapp_notification(lead_data)

    # Add AI message to history
    conv["messages"].append({"role": "assistant", "content": clean_response})
    conversations[call_sid] = conv

    return _re_gather(call_sid, clean_response, is_english)


@app.route("/voice/status", methods=["POST"])
def call_status():
    """Called when call ends"""
    call_sid    = request.form.get("CallSid")
    call_status = request.form.get("CallStatus")
    duration    = request.form.get("CallDuration", "0")

    if call_sid in conversations:
        conv = conversations[call_sid]
        # Save partial data if not already saved
        if conv["data"] and "saved" not in conv["data"]:
            conv["data"]["status"]      = f"Call ended — {call_status}"
            conv["data"]["duration_sec"] = duration
            _save_to_sheets(conv["data"])
        del conversations[call_sid]

    return "", 204


# ─── Helper Functions ─────────────────────────────────────────

def _re_gather(call_sid: str, message: str, is_english: bool = False) -> Response:
    """Speak message and listen for response"""
    resp    = VoiceResponse()
    voice   = "Polly.Joanna" if is_english else "Polly.Aditi"
    lang    = "en-US" if is_english else "hi-IN"
    speech_lang = "en-IN" if is_english else "hi-IN"

    gather = Gather(
        input="speech",
        action=f"/voice/respond?call_sid={call_sid}",
        method="POST",
        language=speech_lang,
        speech_timeout="auto",
        enhanced=True
    )
    gather.say(message, voice=voice, language=lang)
    resp.append(gather)

    # Fallback if no response
    resp.say(
        "Thank you for calling GSLX. We will follow up shortly!" if is_english
        else "GSLX mein call karne ka shukriya. Hum jald hi aapse sampark karenge!",
        voice=voice, language=lang
    )
    return Response(str(resp), mimetype="text/xml")


def _get_ai_response(messages: list, is_english: bool) -> str:
    """Get response from Claude"""
    try:
        system = SYSTEM_PROMPT
        if is_english:
            system += "\n\nNote: This caller speaks English. Respond in English only."

        response = anthropic_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            system=system,
            messages=messages
        )
        return response.content[0].text
    except Exception as e:
        print(f"Claude error: {e}")
        return "Thoda technical issue aa gaya. Kya aap thodi der baad call kar sakte hain?"


def _extract_lead_data(text: str) -> dict | None:
    """Extract lead JSON from AI response if present"""
    match = re.search(r'\[LEAD_DATA:({.*?})\]', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except:
            return None
    return None


def _clean_response(text: str) -> str:
    """Remove hidden JSON from spoken response"""
    return re.sub(r'\[LEAD_DATA:.*?\]', '', text, flags=re.DOTALL).strip()


def _is_english(text: str) -> bool:
    """Simple check if text is mostly English"""
    english_words = ["hello", "hi", "yes", "no", "please", "thank", "need", "want", "help", "service", "website", "security"]
    text_lower = text.lower()
    return any(w in text_lower for w in english_words)


def _save_to_sheets(data: dict):
    """Save lead data to Google Sheets"""
    try:
        sheet = get_sheet()

        # Add header if sheet is empty
        if sheet.row_count == 0 or not sheet.cell(1, 1).value:
            sheet.append_row([
                "Date/Time", "Name", "Company", "Phone",
                "Requirement", "Budget", "Status", "Language", "Duration (sec)"
            ])

        sheet.append_row([
            data.get("call_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            data.get("name", ""),
            data.get("company", ""),
            data.get("phone", ""),
            data.get("requirement", ""),
            data.get("budget", ""),
            data.get("status", ""),
            data.get("language", ""),
            data.get("duration_sec", "")
        ])
        print(f"✅ Lead saved: {data.get('name')} — {data.get('requirement')}")
    except Exception as e:
        print(f"❌ Sheets error: {e}")


def _send_whatsapp_notification(data: dict):
    """Send WhatsApp notification when lead is captured"""
    try:
        whatsapp_to   = f"whatsapp:{os.environ.get('OWNER_WHATSAPP', '+918929031413')}"
        whatsapp_from = f"whatsapp:{os.environ.get('TWILIO_WHATSAPP_NUMBER', '')}"

        if not os.environ.get('TWILIO_WHATSAPP_NUMBER'):
            print("WhatsApp not configured, skipping notification")
            return

        message = (
            f"🔥 *New GSLX Lead!*\n\n"
            f"👤 Name: {data.get('name', 'N/A')}\n"
            f"🏢 Company: {data.get('company', 'N/A')}\n"
            f"📞 Phone: {data.get('phone', 'N/A')}\n"
            f"💼 Need: {data.get('requirement', 'N/A')}\n"
            f"💰 Budget: {data.get('budget', 'N/A')}\n"
            f"🎯 Status: {data.get('status', 'N/A')}\n"
            f"🕐 Time: {data.get('call_time', 'N/A')}"
        )

        twilio_client.messages.create(
            body=message,
            from_=whatsapp_from,
            to=whatsapp_to
        )
        print(f"✅ WhatsApp notification sent")
    except Exception as e:
        print(f"❌ WhatsApp error: {e}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
