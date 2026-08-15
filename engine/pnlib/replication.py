
from __future__ import annotations
import os, json, time, shutil, hashlib, subprocess

def _digest(job: dict, workspace: str) -> str:

    return job.get("result_hash") or hashlib.sha256(workspace.encode()).hexdigest()

class ReplicationTarget:

    name = "base"

    def push(self, job: dict, workspace: str) -> dict:
        raise NotImplementedError

class LocalDirTarget(ReplicationTarget):

    name = "local-dir"

    ERGEBNIS = ("artifacts", "inputs", "log", "logs", "state", ".git",
                "MANIFEST.json", "README.md", "provenance.json", "GEKUERZT.json")

    def __init__(self, base_dir: str):
        self.base_dir = base_dir

    @classmethod
    def _nur_ergebnis(cls, wurzel):

        w = os.path.abspath(wurzel)

        def ignoriere(verzeichnis, namen):
            if os.path.abspath(verzeichnis) != w:
                return set()
            return {n for n in namen if n not in cls.ERGEBNIS}
        return ignoriere

    def push(self, job: dict, workspace: str) -> dict:
        digest = _digest(job, workspace)
        dest = os.path.join(self.base_dir, f"{job['id']}-{digest[:16]}")
        try:
            if os.path.isdir(dest):
                return {"ok": True, "result_uri": "file://" + dest,
                        "result_hash": digest, "idempotent": True, "target": self.name}
            os.makedirs(self.base_dir, exist_ok=True)
            tmp = dest + ".partial"
            if os.path.isdir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)
            shutil.copytree(workspace, tmp, ignore=self._nur_ergebnis(workspace))
            os.replace(tmp, dest)
            return {"ok": True, "result_uri": "file://" + dest,
                    "result_hash": digest, "target": self.name}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}", "target": self.name}

class RsyncTarget(ReplicationTarget):

    name = "rsync"

    def __init__(self, dest_root: str, *, ssh_opts=None, runner=None, exists_runner=None):
        self.dest_root = dest_root.rstrip("/")
        self.ssh_opts = ssh_opts or os.environ.get("PN_REPLICA_RSYNC_SSH",
                                                    "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new")
        self._run = runner or (lambda argv: subprocess.run(
            argv, capture_output=True, text=True, timeout=600))

        self._exists = exists_runner

    def _dest(self, job, digest):
        return f"{self.dest_root}/{job['id']}-{digest[:16]}"

    def push(self, job: dict, workspace: str) -> dict:
        digest = _digest(job, workspace)
        dest = self._dest(job, digest)

        if self._exists is not None:
            try:
                if self._exists(dest):
                    return {"ok": True, "result_uri": "rsync://" + dest, "result_hash": digest,
                            "idempotent": True, "target": self.name}
            except Exception:
                pass

        argv = ["rsync", "-a", "--mkpath", "--delete", "-e", self.ssh_opts,
                workspace.rstrip("/") + "/", dest + "/"]
        try:
            r = self._run(argv)
            if getattr(r, "returncode", 1) == 0:
                return {"ok": True, "result_uri": "rsync://" + dest, "result_hash": digest,
                        "target": self.name}
            err = (getattr(r, "stderr", "") or "").strip()[:300]
            return {"ok": False, "error": f"rsync rc={r.returncode}: {err}", "target": self.name}
        except FileNotFoundError:
            return {"ok": False, "error": "rsync binary not found", "target": self.name}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}", "target": self.name}

class GitRemoteTarget(ReplicationTarget):

    name = "git-remote"

    def __init__(self, remote: str, *, runner=None):
        self.remote = remote
        self._run = runner or (lambda argv, cwd: subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=600))

    def push(self, job: dict, workspace: str) -> dict:
        digest = _digest(job, workspace)
        if not os.path.isdir(os.path.join(workspace, ".git")):
            return {"ok": False, "error": "workspace is not a git repo (no record commit)",
                    "target": self.name}
        ref = f"refs/records/{job['id']}"
        argv = ["git", "push", self.remote, f"HEAD:{ref}"]
        try:
            r = self._run(argv, workspace)
            rc = getattr(r, "returncode", 1)
            out = ((getattr(r, "stderr", "") or "") + (getattr(r, "stdout", "") or "")).lower()
            if rc == 0:

                idem = "up-to-date" in out or "up to date" in out
                return {"ok": True, "result_uri": f"git+{self.remote}#{ref}",
                        "result_hash": digest, "idempotent": idem, "target": self.name}
            return {"ok": False, "error": f"git push rc={rc}: {out.strip()[:300]}",
                    "target": self.name}
        except FileNotFoundError:
            return {"ok": False, "error": "git binary not found", "target": self.name}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}", "target": self.name}

class S3Target(ReplicationTarget):

    name = "s3"

    def __init__(self, bucket: str, prefix: str = "records", *, client=None, endpoint_url=None):
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = client
        self.endpoint_url = endpoint_url

    def _key(self, job, digest):
        return f"{self.prefix}/{job['id']}-{digest[:16]}.tar"

    def push(self, job: dict, workspace: str) -> dict:
        digest = _digest(job, workspace)
        key = self._key(job, digest)
        uri = f"s3://{self.bucket}/{key}"
        if self.client is None:
            return {"ok": False, "error": "s3 client not wired (no boto3/credentials configured)",
                    "target": self.name}
        try:

            try:
                self.client.head_object(Bucket=self.bucket, Key=key)
                return {"ok": True, "result_uri": uri, "result_hash": digest,
                        "idempotent": True, "target": self.name}
            except Exception:
                pass
            import io, tarfile
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tf:
                tf.add(workspace, arcname=str(job["id"]))
            buf.seek(0)
            self.client.put_object(Bucket=self.bucket, Key=key, Body=buf.getvalue())
            return {"ok": True, "result_uri": uri, "result_hash": digest, "target": self.name}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}", "target": self.name}

def from_config(cfg: dict | None = None):

    rep = (cfg or {}).get("replication") if cfg else None
    kind = (rep or {}).get("target") if rep else None
    if kind == "local-dir" and rep.get("dir"):
        return LocalDirTarget(rep["dir"])
    if kind == "rsync" and rep.get("dest_root"):
        return RsyncTarget(rep["dest_root"], ssh_opts=rep.get("ssh_opts"))
    if kind == "git-remote" and rep.get("remote"):
        return GitRemoteTarget(rep["remote"])
    if kind == "s3" and rep.get("bucket"):
        return S3Target(rep["bucket"], rep.get("prefix", "records"),
                        endpoint_url=rep.get("endpoint_url"))

    if os.environ.get("PN_REPLICA_RSYNC"):
        return RsyncTarget(os.environ["PN_REPLICA_RSYNC"])
    if os.environ.get("PN_REPLICA_GIT"):
        return GitRemoteTarget(os.environ["PN_REPLICA_GIT"])
    if os.environ.get("PN_REPLICA_S3"):
        b = os.environ["PN_REPLICA_S3"]
        bucket, _, prefix = b.partition("/")
        return S3Target(bucket, prefix or "records")
    if os.environ.get("PN_REPLICA_DIR"):
        return LocalDirTarget(os.environ["PN_REPLICA_DIR"])
    return None
