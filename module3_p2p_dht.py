import os
import hashlib
import json
import time
import base64
from typing import Dict, Any

class Module3P2PDHT:
    """
    Phase 3: Decentralized P2P Scattering & Stego Magnet URI Generator
    Generates deterministic SHA-256 swarm hashes and packages metadata
    into secure Stego Magnet Links (stego://) with TTL expiration.
    """

    def __init__(self, ttl_seconds: int = 86400):
        self.ttl_seconds = ttl_seconds

    def compute_file_infohash(self, file_path: str) -> str:
        """Computes SHA-256 info-hash for P2P swarm indexing."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    def generate_stego_magnet_uri(self, stego_dir: str, session_id: str, metadata_path: str = "fragments/metadata.json") -> str:
        """Constructs a custom Stego Magnet URI with embedded metadata and TTL."""
        img_path = os.path.join(stego_dir, "stego_cover.png")
        aud_path = os.path.join(stego_dir, "stego_cover.wav")
        txt_path = os.path.join(stego_dir, "stego_cover.txt")

        meta_content = {}
        if os.path.exists(metadata_path):
            with open(metadata_path, "r", encoding="utf-8") as f:
                meta_content = json.load(f)

        payload_manifest = {
            "session_id": session_id,
            "timestamp": int(time.time()),
            "ttl": self.ttl_seconds,
            "metadata": meta_content,
            "hashes": {
                "img": self.compute_file_infohash(img_path),
                "aud": self.compute_file_infohash(aud_path),
                "txt": self.compute_file_infohash(txt_path),
            }
        }

        raw_json = json.dumps(payload_manifest).encode("utf-8")
        encoded_manifest = base64.urlsafe_b64encode(raw_json).decode("utf-8")

        return f"stego://dht?xt={encoded_manifest}"

    def parse_stego_magnet_uri(self, uri: str) -> Dict[str, Any]:
        """Parses and validates incoming stego:// Magnet Link for TTL validity."""
        if not uri.startswith("stego://dht?xt="):
            raise ValueError("Invalid Stego Magnet URI format.")

        # Extract only the Base64 xt payload (strip out extra URL params like &addr=)
        xt_part = uri.split("stego://dht?xt=")[1]
        if "&" in xt_part:
            encoded_manifest = xt_part.split("&")[0]
        else:
            encoded_manifest = xt_part

        # Auto-fix missing Base64 padding if needed
        missing_padding = len(encoded_manifest) % 4
        if missing_padding:
            encoded_manifest += "=" * (4 - missing_padding)

        decoded_json = base64.urlsafe_b64decode(encoded_manifest.encode("utf-8")).decode("utf-8")
        manifest = json.loads(decoded_json)

        # Check the structure before using any field of an untrusted link
        if not isinstance(manifest, dict):
            raise ValueError("Invalid Stego Magnet URI: manifest is not an object.")
        for key in ("timestamp", "ttl", "metadata", "hashes"):
            if key not in manifest:
                raise ValueError(f"Invalid Stego Magnet URI: missing '{key}'.")
        if not isinstance(manifest["timestamp"], (int, float)) or not isinstance(manifest["ttl"], (int, float)):
            raise ValueError("Invalid Stego Magnet URI: bad timestamp or TTL.")
        for key in ("img", "aud", "txt"):
            if key not in manifest["hashes"]:
                raise ValueError(f"Invalid Stego Magnet URI: missing hash '{key}'.")

        # Enforce TTL Expiration
        created_at = manifest["timestamp"]
        ttl = manifest["ttl"]
        if time.time() > (created_at + ttl):
            raise PermissionError("Stego Magnet Link has expired (TTL elapsed).")

        return manifest


if __name__ == "__main__":
    p2p = Module3P2PDHT()
    magnet_link = p2p.generate_stego_magnet_uri("stego_carriers", "SESSION_KEY_2026_TEST")
    print("--- GENERATED STEGO MAGNET LINK ---")
    print(magnet_link)