import os
import json
import time
import base64
import tempfile
import numpy as np
import pytest
import cv2
import scipy.io.wavfile as wav
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from module1_core import Module1LockAndSplit, _aad, _split_sizes, FORMAT_VERSION
from module2_embedding import Module2StegoEmbedding
from module3_p2p_dht import Module3P2PDHT


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


def _make_fragments(ws, engine=None, password="pw-1234", content=b"hardening test payload", name="secret.txt"):
    engine = engine or Module1LockAndSplit(iterations=10_000)
    src = os.path.join(ws, name)
    with open(src, "wb") as f:
        f.write(content)
    frag = os.path.join(ws, "frag")
    engine.encrypt_and_split(src, password, output_dir=frag)
    return frag


def _edit_metadata(frag, **changes):
    path = os.path.join(frag, "metadata.json")
    meta = json.load(open(path))
    meta.update(changes)
    json.dump(meta, open(path, "w"))


def _build_package(ws, version, name, content, password="pw-1234", iterations=10_000):
    """Hand-build a package (to test legacy files and hostile names the normal encryptor cannot produce)."""
    engine = Module1LockAndSplit(iterations=iterations)
    salt, iv = os.urandom(16), os.urandom(12)
    key = engine._derive_key(password, salt, iterations)
    frag = os.path.join(ws, "pkg")
    os.makedirs(frag, exist_ok=True)
    if version == 1:
        sizes = _split_sizes(len(content) + 16)
        ct = AESGCM(key).encrypt(iv, content, None)
        meta = {"original_filename": name, "salt": base64.b64encode(salt).decode(), "iv": base64.b64encode(iv).decode(), "sizes": sizes}
    else:
        name_b = name.encode("utf-8")
        plain = len(name_b).to_bytes(2, "big") + name_b + content
        sizes = _split_sizes(len(plain) + 16)
        ct = AESGCM(key).encrypt(iv, plain, _aad(FORMAT_VERSION, iterations, sizes))
        meta = {"v": 2, "salt": base64.b64encode(salt).decode(), "iv": base64.b64encode(iv).decode(), "iterations": iterations, "sizes": sizes}
    parts = [ct[:sizes[0]], ct[sizes[0]:sizes[0] + sizes[1]], ct[sizes[0] + sizes[1]:]]
    for n, p in zip(("fragment_A_image.bin", "fragment_B_audio.bin", "fragment_C_text.bin"), parts):
        open(os.path.join(frag, n), "wb").write(p)
    json.dump(meta, open(os.path.join(frag, "metadata.json"), "w"))
    return frag


# ---- Module 1: file name and metadata protection ---------------------------------
def test_file_name_is_not_stored_in_clear(workspace):
    frag = _make_fragments(workspace, name="very_secret_report.pdf")
    assert "very_secret_report" not in open(os.path.join(frag, "metadata.json")).read()
    assert "original_filename" not in open(os.path.join(frag, "metadata.json")).read()
    for n in ("fragment_A_image.bin", "fragment_B_audio.bin", "fragment_C_text.bin"):
        assert b"very_secret_report" not in open(os.path.join(frag, n), "rb").read()


def test_file_name_is_restored_from_inside_the_ciphertext(workspace):
    frag = _make_fragments(workspace, name="report \u00e9\u00e8.txt")
    out = Module1LockAndSplit(iterations=10_000).reassemble_and_decrypt("pw-1234", output_dir=frag, restored_dir=os.path.join(workspace, "out"))
    assert os.path.basename(out) == "report \u00e9\u00e8.txt"
    assert open(out, "rb").read() == b"hardening test payload"


def test_changed_sizes_in_metadata_are_detected(workspace):
    frag = _make_fragments(workspace)
    meta = json.load(open(os.path.join(frag, "metadata.json")))
    a, b, c = meta["sizes"]
    _edit_metadata(frag, sizes=[a + 1, b - 1, c])
    with pytest.raises(ValueError, match="INTEGRITY_FAILURE"):
        Module1LockAndSplit(iterations=10_000).reassemble_and_decrypt("pw-1234", output_dir=frag, restored_dir=workspace)


def test_changed_iteration_count_is_detected(workspace):
    frag = _make_fragments(workspace)
    _edit_metadata(frag, iterations=20_000)
    with pytest.raises(ValueError, match="INTEGRITY_FAILURE"):
        Module1LockAndSplit(iterations=10_000).reassemble_and_decrypt("pw-1234", output_dir=frag, restored_dir=workspace)


def test_absurd_iteration_count_in_metadata_is_rejected(workspace):
    frag = _make_fragments(workspace)
    _edit_metadata(frag, iterations=10**12)
    with pytest.raises(ValueError, match="INTEGRITY_FAILURE"):
        Module1LockAndSplit(iterations=10_000).reassemble_and_decrypt("pw-1234", output_dir=frag, restored_dir=workspace)


def test_missing_metadata_fields_fail_cleanly(workspace):
    frag = _make_fragments(workspace)
    meta_path = os.path.join(frag, "metadata.json")
    meta = json.load(open(meta_path)); del meta["salt"]
    json.dump(meta, open(meta_path, "w"))
    with pytest.raises(ValueError, match="INTEGRITY_FAILURE"):
        Module1LockAndSplit(iterations=10_000).reassemble_and_decrypt("pw-1234", output_dir=frag, restored_dir=workspace)


def test_metadata_stores_iterations_so_engines_always_agree(workspace):
    frag = _make_fragments(workspace, Module1LockAndSplit(iterations=10_000))
    out = Module1LockAndSplit(iterations=50_000).reassemble_and_decrypt("pw-1234", output_dir=frag, restored_dir=os.path.join(workspace, "out"))
    assert open(out, "rb").read() == b"hardening test payload"


def test_hostile_file_name_inside_ciphertext_cannot_escape_output_folder(workspace):
    frag = _build_package(workspace, 2, "..\\..\\evil.bat", b"data")
    out_dir = os.path.join(workspace, "out", "inner")
    path = Module1LockAndSplit(iterations=10_000).reassemble_and_decrypt("pw-1234", output_dir=frag, restored_dir=out_dir)
    assert os.path.dirname(os.path.abspath(path)) == os.path.abspath(out_dir)
    assert os.path.basename(path) == "evil.bat"
    assert not os.path.exists(os.path.join(workspace, "evil.bat"))


def test_older_format_1_sessions_still_decrypt(workspace):
    frag = _build_package(workspace, 1, "old_report.txt", b"legacy content")
    out = Module1LockAndSplit(iterations=10_000).reassemble_and_decrypt("pw-1234", output_dir=frag, restored_dir=os.path.join(workspace, "out"))
    assert os.path.basename(out) == "old_report.txt" and open(out, "rb").read() == b"legacy content"


# ---- Module 2 ---------------------------------------------------------------------
def test_corrupted_length_header_is_reported_not_crashed(workspace):
    stego = Module2StegoEmbedding()
    cover = os.path.join(workspace, "c.png")
    cv2.imwrite(cover, np.random.randint(0, 256, (20, 20, 3), dtype=np.uint8))
    out = os.path.join(workspace, "s.png")
    stego.embed_in_image(b"hello", cover, out)
    img = cv2.imread(out)
    flat = img.flatten()
    flat[:16] |= 1
    cv2.imwrite(out, flat.reshape(img.shape))
    with pytest.raises(ValueError):
        stego.extract_from_image(out)


def test_float_wav_is_rejected_with_clear_message(workspace):
    stego = Module2StegoEmbedding()
    cover = os.path.join(workspace, "f.wav")
    wav.write(cover, 44100, np.zeros(5000, dtype=np.float32))
    with pytest.raises(ValueError, match="integer PCM"):
        stego.embed_in_audio(b"x", cover, os.path.join(workspace, "o.wav"))


def test_8bit_wav_roundtrip(workspace):
    stego = Module2StegoEmbedding()
    cover = os.path.join(workspace, "u8.wav")
    wav.write(cover, 8000, np.random.randint(0, 256, 4000, dtype=np.uint8))
    out = os.path.join(workspace, "o.wav")
    stego.embed_in_audio(b"eight bit", cover, out)
    assert stego.extract_from_audio(out) == b"eight bit"


def test_zero_width_characters_already_in_cover_do_not_corrupt_payload(workspace):
    stego = Module2StegoEmbedding()
    cover = os.path.join(workspace, "c.txt")
    open(cover, "w", encoding="utf-8").write("Hello\u200b world\u200c!")
    out = os.path.join(workspace, "s.txt")
    stego.embed_in_text(b"payload", cover, out)
    assert stego.extract_from_text(out) == b"payload"


def test_png_required_for_stego_image(workspace):
    stego = Module2StegoEmbedding()
    cover = os.path.join(workspace, "c.png")
    cv2.imwrite(cover, np.zeros((30, 30, 3), dtype=np.uint8))
    with pytest.raises(ValueError, match="PNG"):
        stego.embed_in_image(b"x", cover, os.path.join(workspace, "out.jpg"))


# ---- Module 3 ---------------------------------------------------------------------
def _write_carriers(ws):
    d = os.path.join(ws, "stego")
    os.makedirs(d)
    for n, content in (("stego_cover.png", b"img"), ("stego_cover.wav", b"aud"), ("stego_cover.txt", b"txt")):
        open(os.path.join(d, n), "wb").write(content)
    return d


def test_expired_magnet_link_is_rejected(workspace):
    p2p = Module3P2PDHT(ttl_seconds=1)
    uri = p2p.generate_stego_magnet_uri(_write_carriers(workspace), "S1", metadata_path=os.path.join(workspace, "none.json"))
    assert p2p.parse_stego_magnet_uri(uri)["ttl"] == 1
    time.sleep(1.2)
    with pytest.raises(PermissionError, match="expired"):
        p2p.parse_stego_magnet_uri(uri)


def test_hashes_in_manifest_match_files_and_detect_change(workspace):
    p2p = Module3P2PDHT()
    d = _write_carriers(workspace)
    manifest = p2p.parse_stego_magnet_uri(p2p.generate_stego_magnet_uri(d, "S2", metadata_path=os.path.join(workspace, "none.json")))
    assert manifest["hashes"]["img"] == p2p.compute_file_infohash(os.path.join(d, "stego_cover.png"))
    open(os.path.join(d, "stego_cover.png"), "ab").write(b"x")
    assert manifest["hashes"]["img"] != p2p.compute_file_infohash(os.path.join(d, "stego_cover.png"))


def test_invalid_or_incomplete_links_are_rejected():
    p2p = Module3P2PDHT()
    with pytest.raises(ValueError):
        p2p.parse_stego_magnet_uri("http://example.com")
    bad = base64.urlsafe_b64encode(json.dumps({"timestamp": time.time(), "ttl": 100}).encode()).decode()
    with pytest.raises(ValueError, match="missing"):
        p2p.parse_stego_magnet_uri("stego://dht?xt=" + bad)
