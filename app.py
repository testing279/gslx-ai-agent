from flask import Flask, request, Response
import anthropic
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import os, json, re

app = Flask(__name__)
anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

def get_sheet():
    creds_json = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
    creds = Credentials.from_service_account_info(creds_json, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    gc = gspread.authorize(creds)
    return gc.open_by_key(os.environ["GOOGLE_SHEET_ID"]).sheet1

conversations = {}

SYSTEM_PROMPT = """You are Ankit, sales rep at GSLX. Speak Hinglish. Max 2-3 sentences per response.
PLANS: Silver Rs45K/month, Gold Rs1.2L/month, Platinum Rs3L/month.
WEB: Rs25K-2L. Collect Name+Company+Requirement+Budget then add:
[LEAD_DATA:{"name":"...","company":"...","requirement":"...","budget":"...","status":"hot/warm/cold"}]"""

@app.route("/", methods=["GET"])
def home():
    return "GSLX AI Live!", 200

@app.route("/voice/incoming", methods=["GET","POST"])
def incoming_call():
    sid = request.values.get("CallSid", request.values.get("CallGuid","x"))
    conversations[sid] = {"messages":[], "caller": request.values.get("From","?"), "data":{}}
    return _resp("Namaste! GSLX mein aapka swagat hai. Main Ankit hoon. Aapki kya help kar sakta hoon?", sid)

@app.route("/voice/respond", methods=["GET","POST"])
def respond():
    sid = request.values.get("CallSid", request.values.get("call_sid","x"))
    speech = request.values.get("SpeechResult","")
    if not speech:
        return _resp("Dobara bolein please?", sid)
    conv = conversations.get(sid, {"messages":[], "caller":"?", "data":{}})
    conv["messages"].append({"role":"user","content":speech})
    try:
        r = anthropic_client.messages.create(model="claude-sonnet-4-20250514", max_tokens=200, system=SYSTEM_PROMPT, messages=conv["messages"])
        ai = r.content[0].text
    except:
        ai = "Technical issue. Baad mein call karein."
    lead = re.search(r'\[LEAD_DATA:({.*?})\]', ai, re.DOTALL)
    if lead:
        try:
            d = json.loads(lead.group(1))
            d["phone"] = conv["caller"]
            d["call_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            try:
                sheet = get_sheet()
                sheet.append_row([d.get("call_time"), d.get("name"), d.get("company"), d.get("phone"), d.get("requirement"), d.get("budget"), d.get("status")])
            except Exception as e:
                print(f"Sheet error: {e}")
        except: pass
    clean = re.sub(r'\[LEAD_DATA:.*?\]','',ai,flags=re.DOTALL).strip()
    conv["messages"].append({"role":"assistant","content":clean})
    conversations[sid] = conv
    return _resp(clean, sid)

@app.route("/voice/status", methods=["GET","POST"])
def status():
    conversations.pop(request.values.get("CallSid","x"), None)
    return "", 204

def _resp(msg, sid):
    h = request.host
    return Response(f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="female" language="hi">{msg}</Say>
  <GetInput action="https://{h}/voice/respond?call_sid={sid}" method="POST" inputType="speech" timeout="5" speechTimeout="auto" language="hi-IN">
    <Say voice="female" language="hi"> </Say>
  </GetInput>
  <Say voice="female" language="hi">Shukriya GSLX call ke liye!</Say>
</Response>""", mimetype="text/xml")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)), debug=False)
