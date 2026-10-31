import os
import json
import base64
import hashlib
from typing import Tuple, Dict, Any, List
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.exceptions import InvalidTag

# OWASP's Password Storage Cheat Sheet recommends at least 600,000 iterations for PBKDF2-HMAC-SHA256.
DEFAULT_ITERATIONS = 600_000
MIN_ITERATIONS = 1_000          # accepted range when reading metadata (guards against absurd values)
MAX_ITERATIONS = 5_000_000

# Format version 2: the file name is placed INSIDE the encrypted data, and the iteration count and
# fragment sizes are authenticated as AES-GCM "associated data". Version 1 (older sessions) still decrypts.
FORMAT_VERSION = 2
NAME_LEN_BYTES = 2
MAX_NAME_BYTES = 255


def _safe_filename(name: str) -> str:
    """Keep only the last path component, so a file name can never write outside the output folder."""
    cleaned = os.path.basename(str(name).replace("\\", "/")).strip()
    if cleaned in ("", ".", ".."):
        return "restored_file"
    return cleaned


def _aad(version: int, iterations: int, sizes: List[int]) -> bytes:
    """Data that is authenticated but not encrypted: any change to it makes decryption fail."""
    return f"STEGO|v{version}|it={iterations}|sizes={','.join(str(int(s)) for s in sizes)}".encode("utf-8")


def _split_sizes(total_len: int) -> List[int]:
    p1 = total_len // 3
    p2 = (total_len * 2) // 3
    return [p1, p2 - p1, total_len - p2]


class Module1LockAndSplit:
    def __init__(self, iterations: int = DEFAULT_ITERATIONS):
        self.iterations = iterations

    def _derive_key(self, password: str, salt: bytes, iterations: int = None) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=iterations or self.iterations,
        )
        return kdf.derive(password.encode("utf-8"))

    def encrypt_and_split(self, file_path: str, password: str, output_dir: str = "fragments") -> Tuple[Tuple[str, str, str], Dict[str, Any]]:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Input file not found: {file_path}")

        os.makedirs(output_dir, exist_ok=True)

        with open(file_path, "rb") as f:
            content = f.read()

        # The file name travels inside the ciphertext, so it never appears in the metadata or in the link
        name_bytes = os.path.basename(file_path).encode("utf-8")[:MAX_NAME_BYTES]
        name_bytes = name_bytes.decode("utf-8", "ignore").encode("utf-8")      # never cut a character in half
        plaintext = len(name_bytes).to_bytes(NAME_LEN_BYTES, "big") + name_bytes + content

        salt = os.urandom(16)
        iv = os.urandom(12)
        key = self._derive_key(password, salt)

        sizes = _split_sizes(len(plaintext) + 16)                  # ciphertext = plaintext + 16-byte tag
        aad = _aad(FORMAT_VERSION, self.iterations, sizes)

        ciphertext = AESGCM(key).encrypt(iv, plaintext, aad)
        frag_a = ciphertext[:sizes[0]]
        frag_b = ciphertext[sizes[0]:sizes[0] + sizes[1]]
        frag_c = ciphertext[sizes[0] + sizes[1]:]

        path_a = os.path.join(output_dir, "fragment_A_image.bin")
        path_b = os.path.join(output_dir, "fragment_B_audio.bin")
        path_c = os.path.join(output_dir, "fragment_C_text.bin")

        with open(path_a, "wb") as f: f.write(frag_a)
        with open(path_b, "wb") as f: f.write(frag_b)
        with open(path_c, "wb") as f: f.write(frag_c)

        metadata = {
            "v": FORMAT_VERSION,
            "salt": base64.b64encode(salt).decode("utf-8"),
            "iv": base64.b64encode(iv).decode("utf-8"),
            "iterations": self.iterations,
            "sizes": sizes,
        }

        with open(os.path.join(output_dir, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=4)

        test_meta = {**metadata,
                     "original_sha256": hashlib.sha256(content).hexdigest(),
                     "part_lengths": sizes}
        return (path_a, path_b, path_c), test_meta

    def reassemble_and_decrypt(self, password: str, output_dir: str = "fragments", restored_dir: str = "restored") -> str:
        meta_path = os.path.join(output_dir, "metadata.json")
        if not os.path.exists(meta_path):
            raise FileNotFoundError("Missing metadata.json for decryption.")

        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        frag_paths = [
            os.path.join(output_dir, "fragment_A_image.bin"),
            os.path.join(output_dir, "fragment_B_audio.bin"),
            os.path.join(output_dir, "fragment_C_text.bin")
        ]
        for p in frag_paths:
            if not os.path.exists(p):
                raise FileNotFoundError(f"Missing fragment file: {p}")

        frags = []
        for p in frag_paths:
            with open(p, "rb") as f:
                frags.append(f.read())

        try:
            version = int(meta.get("v", 1))
            salt = base64.b64decode(meta["salt"])
            iv = base64.b64decode(meta["iv"])
            iterations = int(meta.get("iterations", self.iterations))
        except (KeyError, ValueError, TypeError):
            raise ValueError("INTEGRITY_FAILURE")
        if not (MIN_ITERATIONS <= iterations <= MAX_ITERATIONS):
            raise ValueError("INTEGRITY_FAILURE")

        aad = None
        if version == 2:
            try:
                sizes = [int(s) for s in meta["sizes"]]
            except (KeyError, ValueError, TypeError):
                raise ValueError("INTEGRITY_FAILURE")
            if len(sizes) != 3 or sizes != [len(x) for x in frags]:
                raise ValueError("INTEGRITY_FAILURE")                  # sizes in the metadata were changed
            aad = _aad(version, iterations, sizes)
        elif version != 1:
            raise ValueError("INTEGRITY_FAILURE")

        key = self._derive_key(password, salt, iterations)
        try:
            plaintext = AESGCM(key).decrypt(iv, b"".join(frags), aad)
        except (InvalidTag, ValueError, KeyError, TypeError):
            raise ValueError("INTEGRITY_FAILURE")

        if version == 2:
            if len(plaintext) < NAME_LEN_BYTES:
                raise ValueError("INTEGRITY_FAILURE")
            name_len = int.from_bytes(plaintext[:NAME_LEN_BYTES], "big")
            if NAME_LEN_BYTES + name_len > len(plaintext):
                raise ValueError("INTEGRITY_FAILURE")
            file_name = plaintext[NAME_LEN_BYTES:NAME_LEN_BYTES + name_len].decode("utf-8", "replace")
            content = plaintext[NAME_LEN_BYTES + name_len:]
        else:
            file_name = meta.get("original_filename", "")
            content = plaintext

        os.makedirs(restored_dir, exist_ok=True)
        restored_path = os.path.join(restored_dir, _safe_filename(file_name))
        with open(restored_path, "wb") as f:
            f.write(content)
        return restored_path


if __name__ == "__main__":
    import getpass
    engine = Module1LockAndSplit()
    print("\n--- Phase 1: Lock & Split ---")

    file_input = input("Enter path to file to encrypt (.pdf, .docx, .txt, etc.): ").strip().strip('"')
    if not file_input:
        file_input = "sample_confidential_document.txt"
        if not os.path.exists(file_input):
            with open(file_input, "w", encoding="utf-8") as f:
                f.write("Confidential Capstone Payload Data for Multi-Modal Steganography.")

    pwd_input = getpass.getpass("Enter encryption password: ")
    if not pwd_input:
        raise SystemExit("A password is required.")

    (fa, fb, fc), meta_res = engine.encrypt_and_split(file_input, pwd_input)
    print("\n[+] Lock & Split Completed. Fragments generated in 'fragments/'.")
