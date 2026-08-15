
import base64
import http.client
import json
import ssl

MAILJET_HOST = "api.mailjet.com"
SEND_PATH = "/v3.1/send"

def send(api_key, api_secret, sender, to, subject, text, html=None,
         sender_name="Brainarbeit", reply_to=None, headers=None, timeout=30):

    if not (api_key and api_secret):
        return False, "mailjet not configured (missing key/secret)"
    if not sender:
        return False, "no sender configured"
    if not to:
        return False, "no recipient"
    msg = {
        "From": {"Email": sender, "Name": sender_name or "Brainarbeit"},
        "To": [{"Email": to}],
        "Subject": subject or "(kein Betreff)",
        "TextPart": text or "",
    }
    if html:
        msg["HTMLPart"] = html
    if reply_to:
        msg["ReplyTo"] = {"Email": reply_to}
    if headers:

        msg["Headers"] = {str(k): str(v) for k, v in dict(headers).items()}
    payload = json.dumps({"Messages": [msg]}).encode("utf-8")
    auth = base64.b64encode(("%s:%s" % (api_key, api_secret)).encode()).decode()
    try:
        ctx = ssl.create_default_context()
        c = http.client.HTTPSConnection(MAILJET_HOST, 443, timeout=timeout, context=ctx)
        c.request("POST", SEND_PATH, body=payload, headers={
            "Authorization": "Basic " + auth,
            "Content-Type": "application/json",
        })
        r = c.getresponse(); raw = r.read(); c.close()
    except Exception as e:
        return False, "network error: %s" % type(e).__name__
    try:
        j = json.loads(raw or b"{}")
    except Exception:
        j = {}
    if r.status in (200, 201):
        msgs = j.get("Messages") or []
        st = (msgs[0].get("Status") if (isinstance(msgs, list) and msgs) else "") or ""
        if st == "success":
            mid = ""
            try:
                mid = str(((msgs[0].get("To") or [{}])[0]).get("MessageID", ""))
            except Exception:
                pass
            return True, "sent" + (" id=%s" % mid if mid else "")

        err = ""
        try:
            errs = msgs[0].get("Errors") if (isinstance(msgs, list) and msgs) else None
            if errs:
                err = str(errs[0].get("ErrorMessage", ""))[:160]
        except Exception:
            pass
        return False, "mailjet status=%s%s" % (st or "unknown", (": " + err) if err else "")
    reason = j.get("ErrorMessage") or j.get("ErrorInfo") or ("http %d" % r.status)
    return False, "mailjet error: %s" % str(reason)[:200]

def probe_senders(api_key, api_secret, timeout=20):

    if not (api_key and api_secret):
        return False, "not configured"
    auth = base64.b64encode(("%s:%s" % (api_key, api_secret)).encode()).decode()
    try:
        ctx = ssl.create_default_context()
        c = http.client.HTTPSConnection(MAILJET_HOST, 443, timeout=timeout, context=ctx)
        c.request("GET", "/v3/REST/sender?Limit=100", headers={"Authorization": "Basic " + auth})
        r = c.getresponse(); raw = r.read(); c.close()
    except Exception as e:
        return False, "network error: %s" % type(e).__name__
    if r.status != 200:
        return False, "http %d" % r.status
    try:
        data = json.loads(raw or b"{}").get("Data", [])
    except Exception:
        data = []
    return True, [{"email": x.get("Email"), "status": x.get("Status"), "name": x.get("Name")} for x in data]
