"""rc18 P11-DEP-06: trusted key store, key ids, revocation, signed rotation, git-tree signing, and the shell
integration used by install.sh / update.sh / rollback.sh / offline-install.sh. Disposable test keys only."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release_signing.py"


def run(*args, env=None):
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *map(str, args)], capture_output=True, text=True,
                          timeout=60, env=env)


def keypair(d, name):
    r = run("keygen", "--private", d / f"{name}.pem", "--public", d / f"{name}.pub")
    assert r.returncode == 0, r.stderr
    return r.stdout.split()[-1]


def release(d):
    (d / "app").mkdir(parents=True)
    (d / "app" / "a.py").write_text("print('a')\n"); (d / "release.json").write_text('{"version":"x"}')
    return d


def test_trust_store_signature_tamper_unknown_key_and_replacement(tmp_path):
    keys, store, rel = tmp_path / "keys", tmp_path / "store", release(tmp_path / "rel")
    keys.mkdir()
    fp = keypair(keys, "owner"); keypair(keys, "stranger")
    assert oct((keys / "owner.pem").stat().st_mode & 0o777) == "0o600"
    assert run("verify", "--store", store, "--dir", rel).returncode == 2                    # no trust store
    assert "fingerprint mismatch" in run("trust-init", "--store", store, "--public", keys / "owner.pub",
                                         "--fingerprint", "00" * 32).stderr
    assert run("trust-init", "--store", store, "--public", keys / "owner.pub", "--fingerprint", fp).returncode == 0
    assert run("trust-init", "--store", store, "--public", keys / "stranger.pub", "--fingerprint", fp).returncode == 3
    assert "unsigned release" in run("verify", "--store", store, "--dir", rel).stderr      # missing signature
    assert run("sign", "--private", keys / "owner.pem", "--dir", rel).returncode == 0
    ok = run("verify", "--store", store, "--dir", rel)
    assert ok.returncode == 0 and "signature OK" in ok.stdout
    (rel / "app" / "a.py").write_text("print('evil')\n")
    assert "hash mismatch: app/a.py" in run("verify", "--store", store, "--dir", rel).stderr  # modified package
    (rel / "app" / "a.py").write_text("print('a')\n")
    (rel / "app" / "extra.py").write_text("x")
    assert "unlisted file" in run("verify", "--store", store, "--dir", rel).stderr
    (rel / "app" / "extra.py").unlink()
    (rel / "SHA256SUMS").write_text((rel / "SHA256SUMS").read_text().replace("release.json", "release.jsoN"))
    assert "signature does not match" in run("verify", "--store", store, "--dir", rel).stderr  # invalid signature
    run("sign", "--private", keys / "stranger.pem", "--dir", rel)
    assert "unknown key" in run("verify", "--store", store, "--dir", rel).stderr             # unknown signing key
    run("sign", "--private", keys / "owner.pem", "--dir", rel)
    kid = next(store.glob("*.pub")).name
    shutil.copy(keys / "stranger.pub", store / kid)                                          # key file swapped
    r = run("verify", "--store", store, "--dir", rel)
    assert r.returncode == 2 and "unauthorized replacement" in r.stderr
    shutil.copy(keys / "owner.pub", store / kid)
    shutil.copy(keys / "stranger.pub", store / "deadbeefdeadbeef.pub")                       # key file added
    assert "unrecorded key files" in run("verify", "--store", store, "--dir", rel).stderr
    (store / "deadbeefdeadbeef.pub").unlink()
    t = json.loads((store / "trust.json").read_text())
    t["keys"][kid.removesuffix(".pub")]["revoked"] = False
    t["keys"]["0000000000000000"] = {"fingerprint": "x", "revoked": False, "via": "hand edit"}
    (store / "trust.json").write_text(json.dumps(t))
    assert "edited outside" in run("verify", "--store", store, "--dir", rel).stderr           # trust.json hand edit


def test_controlled_rotation_and_revocation(tmp_path):
    keys, store, rel = tmp_path / "keys", tmp_path / "store", release(tmp_path / "rel")
    keys.mkdir()
    fp = keypair(keys, "old"); keypair(keys, "new"); keypair(keys, "rogue")
    run("trust-init", "--store", store, "--public", keys / "old.pub", "--fingerprint", fp)
    # a rotation statement signed by an untrusted key is refused
    run("rotation", "--private", keys / "rogue.pem", "--new-public", keys / "rogue.pub", "--out", tmp_path / "bad.json")
    assert run("rotate", "--store", store, "--statement", tmp_path / "bad.json").returncode == 3
    # a statement whose new key was swapped after signing is refused
    run("rotation", "--private", keys / "old.pem", "--new-public", keys / "new.pub", "--revoke-current",
        "--out", tmp_path / "rot.json")
    doc = json.loads((tmp_path / "rot.json").read_text())
    forged = json.loads(json.dumps(doc)); forged["statement"]["new_public_pem"] = (keys / "rogue.pub").read_text()
    (tmp_path / "forged.json").write_text(json.dumps(forged))
    assert run("rotate", "--store", store, "--statement", tmp_path / "forged.json").returncode == 3
    r = run("rotate", "--store", store, "--statement", tmp_path / "rot.json")
    assert r.returncode == 0, r.stderr
    run("sign", "--private", keys / "old.pem", "--dir", rel)
    assert "revoked key" in run("verify", "--store", store, "--dir", rel).stderr              # old key revoked
    run("sign", "--private", keys / "new.pem", "--dir", rel)
    assert run("verify", "--store", store, "--dir", rel).returncode == 0
    assert run("rotate", "--store", store, "--statement", tmp_path / "rot.json").returncode == 3   # replay refused


def test_git_tree_signing_and_shell_integration(tmp_path):
    keys, store = tmp_path / "keys", tmp_path / "store"
    keys.mkdir()
    fp = keypair(keys, "owner")
    run("trust-init", "--store", store, "--public", keys / "owner.pub", "--fingerprint", fp)
    repo = tmp_path / "repo"
    release(repo)
    g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)   # noqa: E731
    g("init", "-q"); g("add", "-A"); g("-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "r")
    assert run("sign-git", "--private", keys / "owner.pem", "--repo", repo, "--ref", "HEAD", "--out", repo).returncode == 0
    g("add", "-A"); g("-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "sig"); g("tag", "v1")
    (repo / "untracked.txt").write_text("ignored by git-tree verification")
    assert run("verify-git", "--store", store, "--repo", repo, "--ref", "v1").returncode == 0
    (repo / "app" / "a.py").write_text("print('changed')\n")
    g("-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qam", "unsigned change"); g("tag", "v2")
    assert run("verify-git", "--store", store, "--repo", repo, "--ref", "v2").returncode == 2
    # shell helpers exactly as install/update/rollback call them, with the INSTALLED verifier copy
    verifier = tmp_path / "verifier.py"
    shutil.copy(SCRIPT, verifier)
    (store / "verifier.sha256").write_text(subprocess.run(["sha256sum", str(verifier)], capture_output=True,
                                                          text=True).stdout.split()[0])
    env = dict(os.environ, INM_RELEASE_TRUST_DIR=str(store), INM_RELEASE_VERIFIER=str(verifier),
               INM_ETC_DIR=str(tmp_path / "etc"))
    common = ROOT / "deployment/centos9/common.sh"

    def sh(cmd, **extra):
        return subprocess.run(["bash", "-c", f'source "{common}"; ROOT="{repo}"; {cmd}'], capture_output=True,
                              text=True, env=env | extra, timeout=60)
    assert sh("verify_release_ref v1", INM_MODE="live").returncode == 0
    assert sh("verify_release_ref v2", INM_MODE="live").returncode != 0
    assert sh("verify_release_ref v2", INM_MODE="live", INM_ALLOW_UNSIGNED_RELEASE="1").returncode != 0  # never in live
    dev = sh("verify_release_ref v2", INM_MODE="demo", INM_ALLOW_UNSIGNED_RELEASE="1")
    assert dev.returncode == 0 and "UNSIGNED RELEASE ALLOWED" in dev.stderr
    verifier.write_text(verifier.read_text() + "\n# tampered\n")                     # verifier itself modified
    r = sh("verify_release_ref v1", INM_MODE="live")
    assert r.returncode == 3 and "verifier hash changed" in r.stderr


def test_installer_scripts_call_verification_before_executing_release_content():
    upd = (ROOT / "deployment/centos9/update.sh").read_text()
    assert upd.index("verify_release_ref \"$REF\"") < upd.index("BACKUP before any change")
    rb = (ROOT / "deployment/centos9/rollback.sh").read_text()
    assert rb.index("verify_release_ref \"$REF\"") < rb.index("checkout -q \"$REF\"")
    ins = (ROOT / "deployment/centos9/install.sh").read_text()
    assert ins.index("verify_release_ref HEAD") < ins.index("preflight\n")
    off = (ROOT / "deployment/centos9/offline-install.sh").read_text()
    assert off.index("verify_release_dir \"$REL\"") < off.index("cp -a")
