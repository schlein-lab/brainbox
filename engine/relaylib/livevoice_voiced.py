
import os, base64, tempfile

VOICED_SOCK = os.path.expanduser("~/.local/share/brainbox-portal/voiced.sock")

def voice_request(req, timeout=120, sock=VOICED_SOCK):
    from pnlib import ipc
    return ipc.send_request(req, timeout=timeout, path=sock)

def make_stt_fn(sock=VOICED_SOCK, lang="de"):

    def stt_fn(mic):
        wav = base64.b64decode(mic.get("pcm", ""))
        f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            f.write(wav); f.close()
            r = voice_request({"op": "stt", "path": f.name, "lang": mic.get("lang", lang)}, sock=sock)
            return (r.get("text", ""), bool(mic.get("final", True)))
        finally:
            try: os.unlink(f.name)
            except OSError: pass
    return stt_fn

def make_tts_fn(sock=VOICED_SOCK, chunk_bytes=16000):

    def tts_fn(text):
        out = tempfile.mktemp(suffix=".wav")
        data = b""
        try:
            voice_request({"op": "tts", "text": (text or "")[:3000], "out": out}, sock=sock)
            if os.path.exists(out):
                data = open(out, "rb").read()
        finally:
            try: os.unlink(out)
            except OSError: pass
        for i in range(0, len(data), chunk_bytes):
            yield data[i:i + chunk_bytes]
    return tts_fn
