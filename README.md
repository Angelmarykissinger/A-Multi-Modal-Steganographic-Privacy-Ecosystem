# A Multi-Modal Steganographic Privacy Ecosystem

This project is a proof-of-concept privacy workflow that hides sensitive payloads in multiple carrier media and distributes them through a lightweight, time-limited P2P-style transfer flow. The system is designed as a three-part pipeline:

1. Encrypt and split a file into three fragments.
2. Hide each fragment inside a different carrier type (image, audio, and text).
3. Publish a secure "stego://" magnet-like link that points to three carrying nodes so a recipient can reconstruct the original file only with the correct password.

This repository blends cryptography, steganography, and a minimal network layer into a single educational prototype for exploring covert but controlled data transfer.

## Project Goals

- Protect payload confidentiality with strong encryption before concealment.
- Spread a secret across multiple carriers to reduce single-point exposure.
- Use different steganographic channels for diverse media types.
- Make tampering obvious through integrity checks and structure validation.
- Provide a realistic sender/receiver demo flow with TTL-based expiration.

## How the System Works

### 1) Lock and Split

The module in `module1_core.py` encrypts a file using AES-GCM with PBKDF2-HMAC-SHA256-derived keys. The original file is then split into three ciphertext fragments and stored with metadata that includes the salt, IV, iteration count, and fragment sizes.

Key security features:

- Password-based key derivation using PBKDF2.
- AES-GCM authenticated encryption.
- Fragment sizes embedded in authenticated metadata.
- File name stored inside the encrypted payload rather than in cleartext metadata.
- Reassembly rejects invalid metadata, altered sizes, missing fields, and absurd iteration counts.

### 2) Multi-Modal Steganography

The module in `module2_embedding.py` embeds the ciphertext fragments into three separate carriers:

- Image carrier: LSB embedding in PNG data.
- Audio carrier: LSB embedding in WAV PCM samples.
- Text carrier: Zero-width Unicode characters in a plain text document.

The extraction logic reads the hidden data back from each carrier and reconstructs the original fragment bytes.

### 3) Decentralized P2P Distribution

The module in `module3_p2p_dht.py` creates a custom `stego://dht?xt=...` magnet-style URI containing a JSON manifest with:

- session identifier
- timestamp
- TTL (time-to-live)
- metadata from the encrypted fragments
- SHA-256 hashes for each carrier file

The sender-side pipeline in `backend_api.py` serves the three stego carriers over local sockets on three ports and advertises them in the magnet link. The receiver validates the link, downloads all three carriers, checks the hashes, extracts the hidden fragments, and decrypts the original file.

## Repository Structure

- `module1_core.py` — encryption, fragmentation, reassembly, and integrity checks
- `module2_embedding.py` — image/audio/text steganography embedding and extraction
- `module3_p2p_dht.py` — stego magnet link creation and validation
- `backend_api.py` — sender/receiver orchestration and local seeding logic
- `check_metrics.py` — PSNR/SNR-style quality checks for the generated carriers
- `collect_results.py` — helper for gathering results and comparisons
- `test_module1.py` — tests for encryption, tamper detection, and recovery
- `test_module2.py` — tests for steganography round-trips and capacity limits
- `test_hardening.py` — security-focused hardening and validation tests
- `stego_desktop_app/` — Flutter desktop app project scaffold for a GUI front end
- `cover.png`, `cover.wav`, `cover.tex` — sample media used as covers
- `fragments/` — generated encrypted fragment outputs
- `stego_carriers/` — stego carriers produced by the embedding phase
- `network_received_carriers/` — downloaded carriers at the receiver side
- `real_receiver_output/` — restored output files

## Installation

This project expects Python 3 and a few scientific libraries.

Install the dependencies:

```bash
pip install cryptography numpy opencv-python scipy pytest
```

Optional for desktop app work:

```bash
cd stego_desktop_app
flutter pub get
```

## Running the Demo

### 1) Send a payload

```bash
python backend_api.py --send sample_confidential_document.txt cover.png cover.wav cover.tex
```

Then type the password at the prompt. The system will:

- encrypt and split the file,
- hide it across the three carriers,
- serve those carriers temporarily,
- return a `stego://` magnet-like link.

### 2) Receive the payload

```bash
python backend_api.py --receive "<stego://...>"
```

Then enter the same password when prompted. The receiver will:

- validate the magnet link and TTL,
- download the three carriers,
- verify each carrier hash,
- extract and reassemble the hidden fragments,
- restore the original file in `real_receiver_output/`.

### 3) Optional LAN mode

For local network demos, add `--lan` when sending:

```bash
python backend_api.py --send sample_confidential_document.txt cover.png cover.wav cover.tex --lan
```

This makes the advertised nodes reachable from other devices on the same network.

## Test and Validation

Run the test suite:

```bash
python -m pytest -q
```

The suite checks:

- exact round-trip recovery,
- wrong-password rejection,
- tamper detection,
- metadata manipulation rejection,
- file-name safety,
- zero-byte file handling,
- image and audio capacity guardrails,
- multi-modal extraction integrity.

## Operational Notes

- The project is a research prototype and is not a production-ready secure transport system.
- TTL-based links are time-limited, and the carrier seeding process is intentionally short-lived.
- The code is intentionally explicit about integrity checks and failure modes, which makes it useful for demonstrations and testing.
- The steganography is designed to preserve realistic carrier behavior with minimal visible artifacts, but it is still a controlled environment rather than a hardened deployment standard.

## Example Use Case

A common flow looks like this:

- A confidential document is encrypted and broken into three fragments.
- Fragments are hidden inside an image, a WAV file, and a text file.
- A recipient receives a `stego://` link with three host endpoints.
- The recipient downloads all three carriers, performs integrity checks, and restores the original document with the matching password.

## License and Context

This repository appears to be an academic or portfolio project for exploring secure communication and steganographic privacy. It is intended for learning, experimentation, and controlled demonstrations rather than general-purpose release software.

## Summary

This repository demonstrates how to combine three concepts into one prototype:

- encryption to protect the payload,
- steganography to hide it in common media,
- decentralized routing metadata to transfer the hidden data without exposing the secret in plain sight.

The result is a compact, testable demonstration of a multi-modal privacy ecosystem built in Python, with a desktop app scaffold and sample assets included for experimentation.
