

def _lazy(modname):

    import importlib
    try:
        return importlib.import_module(modname)
    except ImportError as e:
        raise RuntimeError(
            "livechat_portal: portal module %r is unavailable — these adapter factories only run "
            "inside the portal process: %s" % (modname, e))

def make_say_fn(ctx, principal):

    def say_fn(sid, text):
        pc = _lazy("portal_channels")
        return pc.session_say(ctx, principal, {"sid": sid, "text": text, "origin": "livechat"})
    return say_fn

def make_transcript_fn(ctx, principal):

    def transcript_fn(sid):
        pc = _lazy("portal_channels")
        return pc.bus_turns_indexed(ctx, principal, sid)
    return transcript_fn

def make_sessions_fn(ctx, principal):

    def sessions_fn():
        store = ctx["session_store"](principal, "cockpit")
        return store.list()
    return sessions_fn

def make_decisions_fn(principal):

    def decisions_fn():
        mf = _lazy("portal_metafeatures")
        return mf.appr_list(principal, state="open")
    return decisions_fn

def make_decide_fn(principal):

    def decide_fn(aid, decision=None, answer=None):
        mf = _lazy("portal_metafeatures")
        return mf.appr_answer(principal, aid, answer=answer, decision=decision)
    return decide_fn
