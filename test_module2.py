import os
import tempfile
import pytest
import numpy as np
import cv2
import scipy.io.wavfile as wav
from module1_core import Module1LockAndSplit
from module2_embedding import Module2StegoEmbedding

@pytest.fixture
def crypto_engine():
    """Provides Module 1 instance with lower iterations for fast unit testing."""
    return Module1LockAndSplit(iterations=10_000)

@pytest.fixture
def stego_engine():
    """Provides Module 2 instance."""
    return Module2StegoEmbedding()

@pytest.fixture
def temp_workspace():
    """Creates isolated temporary directory for test artifacts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir

@pytest.fixture
def synthetic_covers(temp_workspace):
    """Generates synthetic cover image, audio, and text files with sufficient capacity."""
    img_path = os.path.join(temp_workspace, "cover.png")
    aud_path = os.path.join(temp_workspace, "cover.wav")
    txt_path = os.path.join(temp_workspace, "cover.txt")

    # 1. Image Cover: 800x800 RGB PNG image (1.92 Million bytes capacity)
    synthetic_img = np.random.randint(0, 256, (800, 800, 3), dtype=np.uint8)
    cv2.imwrite(img_path, synthetic_img)

    # 2. Audio Cover: 25-second 44.1 kHz WAV audio (~1.1 Million samples capacity)
    sample_rate = 44100
    duration_sec = 25
    audio_data = (np.sin(2 * np.pi * np.arange(sample_rate * duration_sec) * 440 / sample_rate) * 32767).astype(np.int16)
    wav.write(aud_path, sample_rate, audio_data)

    # 3. Text Cover: Simple text document
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("Confidential Steganography Project Test Document Carrier.")

    return img_path, aud_path, txt_path


# ----------------------------------------------------------------------
# TEST CASE 1: End-to-End Multi-Modal Roundtrip Integrity
# ----------------------------------------------------------------------
def test_full_stego_roundtrip(crypto_engine, stego_engine, temp_workspace, synthetic_covers):
    """Verifies Lock-Split -> Embed -> Extract -> Reassemble-Decrypt bit-for-bit exactness."""
    cover_img, cover_aud, cover_txt = synthetic_covers
    secret_payload = b"Phase 2 Steganography Pipeline Verification: Bit-for-bit accuracy test."
    
    source_file = os.path.join(temp_workspace, "secret_payload.bin")
    with open(source_file, "wb") as f:
        f.write(secret_payload)

    frag_dir = os.path.join(temp_workspace, "fragments")
    stego_dir = os.path.join(temp_workspace, "stego")
    restored_dir = os.path.join(temp_workspace, "restored")
    os.makedirs(stego_dir, exist_ok=True)
    password = "Phase2TestPassword#2026"

    # Step 1: Module 1 Lock & Split
    crypto_engine.encrypt_and_split(source_file, password, output_dir=frag_dir)

    with open(os.path.join(frag_dir, "fragment_A_image.bin"), "rb") as f: frag_a = f.read()
    with open(os.path.join(frag_dir, "fragment_B_audio.bin"), "rb") as f: frag_b = f.read()
    with open(os.path.join(frag_dir, "fragment_C_text.bin"), "rb") as f: frag_c = f.read()

    # Step 2: Module 2 Multi-Modal Hiding
    stego_img = os.path.join(stego_dir, "stego.png")
    stego_aud = os.path.join(stego_dir, "stego.wav")
    stego_txt = os.path.join(stego_dir, "stego.txt")

    stego_engine.embed_in_image(frag_a, cover_img, stego_img)
    stego_engine.embed_in_audio(frag_b, cover_aud, stego_aud)
    stego_engine.embed_in_text(frag_c, cover_txt, stego_txt)

    # Step 3: Module 2 Extraction
    ext_a = stego_engine.extract_from_image(stego_img)
    ext_b = stego_engine.extract_from_audio(stego_aud)
    ext_c = stego_engine.extract_from_text(stego_txt)

    # Save extracted fragments
    ext_frag_dir = os.path.join(temp_workspace, "extracted_fragments")
    os.makedirs(ext_frag_dir, exist_ok=True)

    with open(os.path.join(ext_frag_dir, "fragment_A_image.bin"), "wb") as f: f.write(ext_a)
    with open(os.path.join(ext_frag_dir, "fragment_B_audio.bin"), "wb") as f: f.write(ext_b)
    with open(os.path.join(ext_frag_dir, "fragment_C_text.bin"), "wb") as f: f.write(ext_c)

    # Copy metadata.json to extracted fragments folder
    import shutil
    shutil.copy(os.path.join(frag_dir, "metadata.json"), os.path.join(ext_frag_dir, "metadata.json"))

    # Step 4: Module 1 Reassemble & Decrypt
    restored_path = crypto_engine.reassemble_and_decrypt(password, output_dir=ext_frag_dir, restored_dir=restored_dir)

    with open(restored_path, "rb") as f:
        restored_data = f.read()

    assert restored_data == secret_payload


# ----------------------------------------------------------------------
# TEST CASE 2: Image Capacity Exceeded Exception
# ----------------------------------------------------------------------
def test_image_capacity_exceeded(stego_engine, temp_workspace):
    """Verifies ValueError is raised when embedding payload exceeding image pixel capacity."""
    tiny_img_path = os.path.join(temp_workspace, "tiny.png")
    tiny_img = np.zeros((10, 10, 3), dtype=np.uint8)  # Capacity: 300 bits
    cv2.imwrite(tiny_img_path, tiny_img)

    large_payload = os.urandom(1024)  # 8192 bits
    out_img = os.path.join(temp_workspace, "out.png")

    with pytest.raises(ValueError, match="Cover image too small"):
        stego_engine.embed_in_image(large_payload, tiny_img_path, out_img)


# ----------------------------------------------------------------------
# TEST CASE 3: Audio Capacity Exceeded Exception
# ----------------------------------------------------------------------
def test_audio_capacity_exceeded(stego_engine, temp_workspace):
    """Verifies ValueError is raised when audio carrier duration is too short for payload."""
    short_aud_path = os.path.join(temp_workspace, "short.wav")
    wav.write(short_aud_path, 44100, np.zeros(100, dtype=np.int16))  # 100 samples capacity

    large_payload = os.urandom(512)  # Requires >4000 samples
    out_aud = os.path.join(temp_workspace, "out.wav")

    with pytest.raises(ValueError, match="Cover audio too short"):
        stego_engine.embed_in_audio(large_payload, short_aud_path, out_aud)