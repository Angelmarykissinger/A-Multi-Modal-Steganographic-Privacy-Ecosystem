import os
import cv2
import numpy as np
import scipy.io.wavfile as wav

ZW0 = "\u200B"   # bit 0
ZW1 = "\u200C"   # bit 1
HEADER_BYTES = 4


def _frame(payload: bytes) -> np.ndarray:
    """4-byte big-endian length header + payload, as a flat array of bits (most significant bit first)."""
    full = len(payload).to_bytes(HEADER_BYTES, byteorder="big") + payload
    return np.unpackbits(np.frombuffer(full, dtype=np.uint8))


def _unframe(lsb_bits: np.ndarray) -> bytes:
    """Read the length header, check it against the available bits, and return the payload bytes."""
    if lsb_bits.size < HEADER_BYTES * 8:
        raise ValueError("Carrier is too small to contain a hidden payload.")
    payload_len = int.from_bytes(np.packbits(lsb_bits[:HEADER_BYTES * 8]).tobytes(), byteorder="big")
    end = HEADER_BYTES * 8 + payload_len * 8
    if end > lsb_bits.size:
        raise ValueError("Carrier does not contain a valid hidden payload (length header is inconsistent).")
    return np.packbits(lsb_bits[HEADER_BYTES * 8:end]).tobytes()


class Module2StegoEmbedding:
    # ------------------------------------------------------------------
    # 1. IMAGE STEGANOGRAPHY (PNG LSB)
    # ------------------------------------------------------------------
    def embed_in_image(self, payload: bytes, cover_path: str, output_path: str):
        img = cv2.imread(cover_path)
        if img is None:
            raise FileNotFoundError(f"Cover image not found: {cover_path}")

        bits = _frame(payload)
        if bits.size > img.size:
            raise ValueError(f"Cover image too small. Needs {bits.size} bits, has {img.size}.")

        flat_img = img.flatten().astype(np.uint8)
        flat_img[:bits.size] = (flat_img[:bits.size] & 0xFE) | bits

        stego_img = flat_img.reshape(img.shape)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        if not output_path.lower().endswith(".png"):
            raise ValueError("The stego image must be saved as PNG (a lossless format).")
        cv2.imwrite(output_path, stego_img)

    def extract_from_image(self, stego_path: str) -> bytes:
        img = cv2.imread(stego_path)
        if img is None:
            raise FileNotFoundError(f"Stego image not found: {stego_path}")
        return _unframe(img.flatten() & 1)

    # ------------------------------------------------------------------
    # 2. AUDIO STEGANOGRAPHY (WAV PCM LSB)
    # ------------------------------------------------------------------
    def embed_in_audio(self, payload: bytes, cover_path: str, output_path: str):
        sample_rate, audio_data = wav.read(cover_path)
        if not np.issubdtype(audio_data.dtype, np.integer):
            raise ValueError("Only integer PCM WAV files are supported (16-bit is recommended).")

        bits = _frame(payload)
        if bits.size > audio_data.size:
            raise ValueError(f"Cover audio too short. Needs {bits.size} samples, has {audio_data.size}.")

        flat_audio = audio_data.flatten().copy()
        clear_lsb = ~np.array(1, dtype=flat_audio.dtype)          # works for signed and unsigned integer samples
        flat_audio[:bits.size] = (flat_audio[:bits.size] & clear_lsb) | bits.astype(flat_audio.dtype)

        stego_audio = flat_audio.reshape(audio_data.shape)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        wav.write(output_path, sample_rate, stego_audio)

    def extract_from_audio(self, stego_path: str) -> bytes:
        sample_rate, audio_data = wav.read(stego_path)
        if not np.issubdtype(audio_data.dtype, np.integer):
            raise ValueError("Only integer PCM WAV files are supported.")
        return _unframe((audio_data.flatten() & 1).astype(np.uint8))

    # ------------------------------------------------------------------
    # 3. TEXT STEGANOGRAPHY (Zero-Width Space Mapping)
    # ------------------------------------------------------------------
    def embed_in_text(self, payload: bytes, cover_path: str, output_path: str):
        with open(cover_path, "r", encoding="utf-8") as f:
            cover_text = f.read()
        # Remove zero-width characters already present, so they cannot be confused with the payload
        cover_text = cover_text.replace(ZW0, "").replace(ZW1, "")

        bits = _frame(payload)
        zero_width_stream = "".join(ZW1 if b else ZW0 for b in bits.tolist())

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(cover_text + zero_width_stream)

    def extract_from_text(self, stego_path: str) -> bytes:
        with open(stego_path, "r", encoding="utf-8") as f:
            stego_text = f.read()
        bits = np.array([1 if c == ZW1 else 0 for c in stego_text if c in (ZW0, ZW1)], dtype=np.uint8)
        return _unframe(bits)


if __name__ == "__main__":
    stego = Module2StegoEmbedding()
    out_dir = "stego_carriers"

    print("\n--- Phase 2: Multi-Modal Steganography Embedding ---")
    cover_img = input("Enter path to Cover PNG Image: ").strip().strip('"') or "cover.png"
    cover_aud = input("Enter path to Cover WAV Audio: ").strip().strip('"') or "cover.wav"
    cover_txt = input("Enter path to Cover TXT Document: ").strip().strip('"') or "cover.tex"

    with open("fragments/fragment_A_image.bin", "rb") as f: fa = f.read()
    with open("fragments/fragment_B_audio.bin", "rb") as f: fb = f.read()
    with open("fragments/fragment_C_text.bin", "rb") as f: fc = f.read()

    stego.embed_in_image(fa, cover_img, os.path.join(out_dir, "stego_cover.png"))
    stego.embed_in_audio(fb, cover_aud, os.path.join(out_dir, "stego_cover.wav"))
    stego.embed_in_text(fc, cover_txt, os.path.join(out_dir, "stego_cover.txt"))

    print(f"\n[+] All 3 fragments embedded into stego carriers in '{out_dir}/'.")
