"""
collect_results.py  -  measures the project on THIS computer and writes the files used in the report.

Put this file in the project folder (next to module1_core.py, module2_embedding.py, module3_p2p_dht.py,
and your cover.png / cover.wav / cover.tex), then run:

    python collect_results.py

Optional:
    python collect_results.py --cover-png X.png --cover-wav Y.wav --cover-txt Z.tex
    python collect_results.py --skip-backend        (skip the Flutter-backend timing section)
    python collect_results.py --skip-tests          (skip running pytest)

Output (in the folder  results_output/ ):
    results.json                     all measurements
    cover_vs_stego_actual.png        cover vs stego image from your last real run (if stego_carriers/ exists)
    cover_vs_stego_test.png          cover vs stego image for a 100 KiB test payload
Send results.json and the two PNG files back so the report tables can be filled with your numbers.
Nothing in your fragments/ or stego_carriers/ folders is changed (all tests run in a temporary folder).
"""
import argparse, json, os, sys, time, shutil, tempfile, platform, socket, threading, hashlib, base64
import statistics as st, subprocess, traceback
import numpy as np
import cv2
import scipy
import scipy.io.wavfile as wav

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from module1_core import Module1LockAndSplit
from module2_embedding import Module2StegoEmbedding
from module3_p2p_dht import Module3P2PDHT

OUT = os.path.join(HERE, "results_output")
os.makedirs(OUT, exist_ok=True)
R = {}
FRAG = ("fragment_A_image.bin", "fragment_B_audio.bin", "fragment_C_text.bin")


def ms(t0):
    return round((time.perf_counter() - t0) * 1000, 1)


def psnr_mse(a, b):
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    return mse, (float("inf") if mse == 0 else float(10 * np.log10(255 ** 2 / mse)))


def snr(a, b):
    a = a.astype(np.float64).ravel(); b = b.astype(np.float64).ravel()
    noise = float(np.sum((b - a) ** 2))
    return float("inf") if noise == 0 else float(10 * np.log10(np.sum(a ** 2) / noise))


def label(img, text):
    """Adds a caption strip ABOVE the image (never over it, so no pixels are hidden)."""
    bar = np.full((28, img.shape[1], 3), 255, np.uint8)
    cv2.putText(bar, text, (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA)
    return np.vstack([bar, img])


def comparison_figure(cover_path, stego_path, out_path, title):
    c = cv2.imread(cover_path); s = cv2.imread(stego_path)
    if c is None or s is None or c.shape != s.shape:
        return None
    diff = (c != s).any(axis=2).astype(np.uint8) * 255            # pixels whose LSBs changed
    diff3 = cv2.cvtColor(diff, cv2.COLOR_GRAY2BGR)
    mse, ps = psnr_mse(c, s)
    h = 520
    def fit(x):
        sc = h / x.shape[0]
        return cv2.resize(x, (int(x.shape[1] * sc), h), interpolation=cv2.INTER_NEAREST)
    panels = [label(fit(c), "Cover image"), label(fit(s), "Stego image"),
              label(fit(diff3), "Changed pixels (white)")]
    row = np.hstack(panels)
    foot = np.full((34, row.shape[1], 3), 255, np.uint8)
    cv2.putText(foot, f"{title}  |  MSE = {mse:.4f}   PSNR = {ps:.2f} dB   changed pixels = {100*np.mean(diff>0):.2f}%",
                (6, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.imwrite(out_path, np.vstack([row, foot]))
    return {"mse": round(mse, 5), "psnr_db": round(ps, 2), "changed_pixels_pct": round(100 * float(np.mean(diff > 0)), 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cover-png", default=os.path.join(HERE, "cover.png"))
    ap.add_argument("--cover-wav", default=os.path.join(HERE, "cover.wav"))
    ap.add_argument("--cover-txt", default=os.path.join(HERE, "cover.tex"))
    ap.add_argument("--skip-backend", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    a = ap.parse_args()
    for p in (a.cover_png, a.cover_wav, a.cover_txt):
        if not os.path.exists(p):
            sys.exit(f"Cover file not found: {p}")

    # ---------------- environment ----------------
    import cryptography
    R["environment"] = {
        "platform": platform.platform(), "machine": platform.machine(), "processor": platform.processor(),
        "python": sys.version.split()[0], "cryptography": cryptography.__version__, "numpy": np.__version__,
        "opencv": cv2.__version__, "scipy": scipy.__version__, "date": time.strftime("%Y-%m-%d %H:%M"),
    }

    # ---------------- covers & capacity ----------------
    img = cv2.imread(a.cover_png); sr, aud = wav.read(a.cover_wav)
    txt_chars = len(open(a.cover_txt, encoding="utf-8").read())
    R["covers"] = {
        "image_file": os.path.basename(a.cover_png), "image_shape": list(img.shape), "image_bytes": os.path.getsize(a.cover_png),
        "audio_file": os.path.basename(a.cover_wav), "audio_dtype": str(aud.dtype), "audio_samples": int(aud.size),
        "audio_rate": int(sr), "audio_seconds": round(aud.size / max(1, (aud.shape[1] if aud.ndim > 1 else 1)) / sr, 2),
        "audio_bytes": os.path.getsize(a.cover_wav), "text_file": os.path.basename(a.cover_txt), "text_chars": txt_chars}
    cap_img = img.size // 8 - 4; cap_aud = aud.size // 8 - 4
    R["capacity_bytes"] = {"image": cap_img, "audio": cap_aud,
                           "largest_file_bytes": 3 * min(cap_img, cap_aud) - 16 - 3}
    print(f"[i] capacity: image {cap_img} B, audio {cap_aud} B per fragment")
    if aud.dtype != np.int16:
        print("[!] cover.wav is not 16-bit PCM (dtype %s); the report assumes 16-bit PCM." % aud.dtype)

    c1 = Module1LockAndSplit(); s2 = Module2StegoEmbedding(); p3 = Module3P2PDHT()
    W = tempfile.mkdtemp(prefix="stego_eval_")
    pw = "EvalPass#2026"

    # ---------------- PBKDF2 ----------------
    c1._derive_key("x", b"0" * 16)
    t0 = time.perf_counter()
    for _ in range(5): c1._derive_key("x", b"0" * 16)
    R["pbkdf2_ms"] = round((time.perf_counter() - t0) / 5 * 1000, 1)

    # ---------------- round trip, timing, imperceptibility ----------------
    rows, imp = [], []
    keep = None
    for kib in (1, 10, 100, 300):
        n = kib * 1024
        frag_max = -(-(n + 16) // 3)
        if frag_max > min(cap_img, cap_aud):
            print(f"[skip] {kib} KiB does not fit in your covers"); continue
        d = os.path.join(W, f"run{kib}"); os.makedirs(d)
        src = os.path.join(d, "secret.bin"); data = os.urandom(n); open(src, "wb").write(data)
        fr = os.path.join(d, "fragments"); stg = os.path.join(d, "stego"); os.makedirs(stg)
        T = {}
        t0 = time.perf_counter(); c1.encrypt_and_split(src, pw, output_dir=fr); T["lock"] = ms(t0)
        fa, fb, fc = [open(os.path.join(fr, f), "rb").read() for f in FRAG]
        t0 = time.perf_counter(); s2.embed_in_image(fa, a.cover_png, os.path.join(stg, "stego_cover.png")); T["embed_image"] = ms(t0)
        t0 = time.perf_counter(); s2.embed_in_audio(fb, a.cover_wav, os.path.join(stg, "stego_cover.wav")); T["embed_audio"] = ms(t0)
        t0 = time.perf_counter(); s2.embed_in_text(fc, a.cover_txt, os.path.join(stg, "stego_cover.txt")); T["embed_text"] = ms(t0)
        t0 = time.perf_counter(); link = p3.generate_stego_magnet_uri(stg, "EVAL", os.path.join(fr, "metadata.json")); T["magnet"] = ms(t0)
        man = p3.parse_stego_magnet_uri(link)
        t0 = time.perf_counter()
        ea = s2.extract_from_image(os.path.join(stg, "stego_cover.png")); eb = s2.extract_from_audio(os.path.join(stg, "stego_cover.wav"))
        ec = s2.extract_from_text(os.path.join(stg, "stego_cover.txt")); T["extract"] = ms(t0)
        ex = os.path.join(d, "ex"); os.makedirs(ex)
        for f, b in zip(FRAG, (ea, eb, ec)): open(os.path.join(ex, f), "wb").write(b)
        json.dump(man["metadata"], open(os.path.join(ex, "metadata.json"), "w"))
        t0 = time.perf_counter(); out = c1.reassemble_and_decrypt(pw, ex, os.path.join(d, "restored")); T["decrypt"] = ms(t0)
        ok = open(out, "rb").read() == data
        rows.append({"payload_bytes": n, "fragments": [len(fa), len(fb), len(fc)], "exact_recovery": ok, "link_chars": len(link),
                     "stego_text_bytes": os.path.getsize(os.path.join(stg, "stego_cover.txt")), "ms": T})
        cs = cv2.imread(os.path.join(stg, "stego_cover.png")); mse, ps = psnr_mse(img, cs)
        _, a2 = wav.read(os.path.join(stg, "stego_cover.wav"))
        imp.append({"payload_kib": kib, "fragment_bytes": len(fb), "image_used_pct": round(100 * (len(fa) + 4) * 8 / img.size, 2),
                    "mse": round(mse, 5), "psnr_db": round(ps, 2) if ps != float("inf") else None,
                    "audio_used_pct": round(100 * (len(fb) + 4) * 8 / aud.size, 2),
                    "snr_db": round(snr(aud, a2), 2) if snr(aud, a2) != float("inf") else None})
        print(f"[ok] {kib} KiB  exact={ok}  PSNR={ps:.2f}  total sender+receiver ms={sum(T.values()):.0f}")
        if kib == 100 or keep is None: keep = (d, fr, stg, ex, link, kib)
    R["roundtrip"] = rows; R["imperceptibility"] = imp
    if not rows:
        sys.exit("None of the test payload sizes fit in your covers; use larger covers.")

    # ---------------- comparison figures ----------------
    d, fr, stg, ex, link, kib = keep
    R["figure_test"] = comparison_figure(a.cover_png, os.path.join(stg, "stego_cover.png"),
                                         os.path.join(OUT, "cover_vs_stego_test.png"), f"{kib} KiB test payload")
    actual = os.path.join(HERE, "stego_carriers", "stego_cover.png")
    R["figure_actual"] = None
    if os.path.exists(actual):
        R["figure_actual"] = comparison_figure(a.cover_png, actual, os.path.join(OUT, "cover_vs_stego_actual.png"), "Your real payload")
        if R["figure_actual"] is None:
            print("[!] stego_carriers/stego_cover.png has a different size than the cover; it was made from another cover.")

    # ---------------- negative tests ----------------
    neg = {}
    def attempt(name, fn):
        try:
            fn(); neg[name] = "ACCEPTED (unexpected)"
        except Exception as e:
            neg[name] = f"{type(e).__name__}: {str(e)[:70]}"
    attempt("wrong_password", lambda: c1.reassemble_and_decrypt("WrongPass", ex, os.path.join(d, "r1")))
    t1 = os.path.join(d, "ex_t"); shutil.copytree(ex, t1)
    b = bytearray(open(os.path.join(t1, FRAG[0]), "rb").read()); b[0] ^= 1; open(os.path.join(t1, FRAG[0]), "wb").write(b)
    attempt("one_bit_flipped_in_fragment_A", lambda: c1.reassemble_and_decrypt(pw, t1, os.path.join(d, "r2")))
    t2 = os.path.join(d, "ex_m"); shutil.copytree(ex, t2); open(os.path.join(t2, FRAG[1]), "wb").write(b"")
    attempt("fragment_B_truncated_to_zero", lambda: c1.reassemble_and_decrypt(pw, t2, os.path.join(d, "r3")))
    t3 = os.path.join(d, "ex_x"); shutil.copytree(ex, t3); os.remove(os.path.join(t3, FRAG[2]))
    attempt("fragment_file_missing", lambda: c1.reassemble_and_decrypt(pw, t3, os.path.join(d, "r4")))
    man = p3.parse_stego_magnet_uri(link)
    tw = os.path.join(d, "tamper.wav"); shutil.copy(os.path.join(stg, "stego_cover.wav"), tw)
    bb = bytearray(open(tw, "rb").read()); bb[-1] ^= 1; open(tw, "wb").write(bb)
    neg["one_bit_changed_in_audio_carrier"] = "digest differs (detected)" if p3.compute_file_infohash(tw) != man["hashes"]["aud"] else "NOT detected"
    expired = Module3P2PDHT(ttl_seconds=-1).generate_stego_magnet_uri(stg, "X", os.path.join(fr, "metadata.json"))
    attempt("expired_link", lambda: p3.parse_stego_magnet_uri(expired))
    attempt("invalid_scheme", lambda: p3.parse_stego_magnet_uri("http://example.com"))
    attempt("payload_over_image_capacity", lambda: s2.embed_in_image(os.urandom(cap_img + 1), a.cover_png, os.path.join(d, "o.png")))
    attempt("payload_over_audio_capacity", lambda: s2.embed_in_audio(os.urandom(cap_aud + 1), a.cover_wav, os.path.join(d, "o.wav")))
    open(os.path.join(W, "e.bin"), "wb").close()
    try:
        c1.encrypt_and_split(os.path.join(W, "e.bin"), pw, os.path.join(W, "e_fr"))
        o = c1.reassemble_and_decrypt(pw, os.path.join(W, "e_fr"), os.path.join(W, "e_out"))
        neg["zero_byte_file"] = f"restored, {os.path.getsize(o)} bytes; fragments " + str(json.load(open(os.path.join(W, 'e_fr', 'metadata.json')))["sizes"])
    except Exception as e:
        neg["zero_byte_file"] = f"{type(e).__name__}: {e}"
    cv2.imwrite(os.path.join(d, "lossy.jpg"), cv2.imread(os.path.join(stg, "stego_cover.png")), [cv2.IMWRITE_JPEG_QUALITY, 95])
    try:
        got = s2.extract_from_image(os.path.join(d, "lossy.jpg"))
        neg["stego_image_saved_as_jpeg_q95"] = "payload equal" if got == fa else f"payload destroyed (extracted {len(got)} bytes)"
    except Exception as e:
        neg["stego_image_saved_as_jpeg_q95"] = f"payload destroyed; extractor raised {type(e).__name__}"
    R["negative_tests"] = neg

    # ---------------- loopback transfer (single-port GET protocol) ----------------
    fm = {"img": "stego_cover.png", "aud": "stego_cover.wav", "txt": "stego_cover.txt"}
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0)); srv.listen(5); port = srv.getsockname()[1]
    def handle(conn):
        try:
            k = conn.recv(1024).decode().strip().split(" ")[1]; fp = os.path.join(stg, fm[k]); n = os.path.getsize(fp)
            conn.sendall(n.to_bytes(8, "big"))
            with open(fp, "rb") as f:
                while ch := f.read(65536): conn.sendall(ch)
        finally:
            conn.close()
    def serve():
        while True:
            try: cn, _ = srv.accept()
            except OSError: return
            threading.Thread(target=handle, args=(cn,), daemon=True).start()
    threading.Thread(target=serve, daemon=True).start()
    def dl(kind, save):
        sk = socket.create_connection(("127.0.0.1", port)); sk.sendall(f"GET {kind}".encode())
        buf = b""
        while len(buf) < 8: buf += sk.recv(8 - len(buf))
        n = int.from_bytes(buf, "big"); got = 0
        with open(save, "wb") as f:
            while got < n:
                ch = sk.recv(min(65536, n - got))
                if not ch: break
                f.write(ch); got += len(ch)
        sk.close(); return got
    os.makedirs(os.path.join(d, "net"), exist_ok=True)
    t0 = time.perf_counter(); tot = sum(dl(k, os.path.join(d, "net", fm[k])) for k in fm); dt = ms(t0)
    ok = all(p3.compute_file_infohash(os.path.join(d, "net", fm[k])) == man["hashes"][k] for k in fm)
    R["loopback_transfer"] = {"payload_kib": kib, "total_bytes": tot, "ms": dt, "digests_match": ok}
    srv.close()

    # ---------------- automated tests (pytest) ----------------
    if not a.skip_tests and os.path.exists(os.path.join(HERE, "test_module1.py")):
        try:
            files = [f for f in ("test_module1.py", "test_module2.py") if os.path.exists(os.path.join(HERE, f))]
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"] + files,
                               cwd=HERE, capture_output=True, text=True, timeout=600)
            tail = [l for l in (r.stdout + r.stderr).strip().splitlines() if l.strip()][-1:]
            R["pytest"] = {"files": files, "exit_code": r.returncode, "summary": tail[0] if tail else ""}
        except Exception as e:
            R["pytest"] = {"error": str(e)}
        print("[pytest]", R["pytest"])

    # ---------------- backend (Flutter app) timing ----------------
    be_src = os.path.join(HERE, "backend_api.py")
    if not a.skip_backend and os.path.exists(be_src):
        try:
            busy = []
            for p in (5001, 5002, 5003):                       # the test needs these ports free
                try:
                    socket.create_connection(("127.0.0.1", p), timeout=0.3).close()
                    busy.append(p)                             # something is listening there
                except OSError:
                    pass
            if busy:
                R["backend"] = {"skipped": f"ports {busy} are in use - click 'Stop Seeding & Withdraw Link' in the app and run again"}
            else:
                # run everything in a private copy so that your real fragments/, stego_carriers/ and outputs stay untouched
                BD = os.path.join(W, "backend_copy"); os.makedirs(BD)
                for f in ("backend_api.py", "module1_core.py", "module2_embedding.py", "module3_p2p_dht.py"):
                    shutil.copy(os.path.join(HERE, f), BD)
                be = os.path.join(BD, "backend_api.py")
                def run(args, password=None):
                    t0 = time.perf_counter()
                    r = subprocess.run([sys.executable, be] + args, input=((password + "\n").encode() if password is not None else b""),
                                       capture_output=True, cwd=W, timeout=120)
                    lines = r.stdout.decode(errors="replace").strip().splitlines()
                    return (json.loads(lines[-1]) if lines else {"status": "error", "message": "no output"}), (time.perf_counter() - t0) * 1000
                pay = os.path.join(W, "backend_payload.bin"); open(pay, "wb").write(os.urandom(100 * 1024))
                S, Rv, good = [], [], True
                run(["--stop"])
                for _ in range(6):
                    r1, t1_ = run(["--send", pay, a.cover_png, a.cover_wav, a.cover_txt], pw); S.append(t1_)
                    time.sleep(1)
                    r2, t2_ = run(["--receive", r1.get("magnet", "")], pw); Rv.append(t2_)
                    restored = os.path.join(BD, "real_receiver_output", "backend_payload.bin")
                    good &= r1.get("status") == "success" and r2.get("status") == "success" and os.path.exists(restored) \
                            and open(restored, "rb").read() == open(pay, "rb").read()
                run(["--stop"])
                R["backend"] = {"runs": 6, "all_restored_exactly": good, "sender_ms": [round(x) for x in S], "sender_median_ms": round(st.median(S)),
                                "receiver_ms": [round(x) for x in Rv], "receiver_median_ms": round(st.median(Rv))}
        except Exception as e:
            R["backend"] = {"error": repr(e)}
        print("[backend]", R["backend"])

    json.dump(R, open(os.path.join(OUT, "results.json"), "w"), indent=2)
    shutil.rmtree(W, ignore_errors=True)
    print("\nDone. Files written to:", OUT)
    print("Send back: results.json, cover_vs_stego_actual.png (if created), cover_vs_stego_test.png")


if __name__ == "__main__":
    main()
