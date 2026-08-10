

class App:
    def __init__(self, id, title, engine=None, wasm=None, web=None, container=None, pixel=None):
        self.id = id
        self.title = title
        self.engine = engine
        self.wasm = wasm
        self.web = web
        self.container = container
        self.pixel = pixel

    def as_dict(self):
        return {"id": self.id, "title": self.title, "engine": self.engine, "wasm": self.wasm,
                "web": self.web, "container": self.container, "pixel": bool(self.pixel)}

APPS = {

    "browser": App("browser", "Browser", engine="browser"),

    "terminal": App("terminal", "Terminal", engine="terminal",
                    pixel=["/bin/bash", "-l"]),

    "notes": App("notes", "Notes", web="/apps/notes"),

    "libreoffice": App("libreoffice", "LibreOffice",
                       container="docker.io/linuxserver/libreoffice",
                       pixel=["/usr/bin/soffice", "--writer"]),
}

def get(app_id):
    return APPS.get(app_id)

def register(app):
    APPS[app.id] = app
    return app

def catalogue():
    return {k: v.as_dict() for k, v in APPS.items()}
