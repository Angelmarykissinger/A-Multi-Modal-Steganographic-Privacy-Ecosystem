import os
import hashlib
import tempfile
import pytest
from module1_core import Module1LockAndSplit

@pytest.fixture
def crypto_engine():
    """Fixture to initialize the module engine with lower iterations for fast testing."""
    return Module1LockAndSplit(iterations=10_000)

@pytest.fixture
def temp_workspace():
    """Fixture to provide an isolated temporary directory for test artifacts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir

# ----------------------------------------------------------------------
# TEST CASE 1: Round-Trip Data Integrity (Bit-for-Bit Exact Recovery)
# ----------------------------------------------------------------------
def test_roundtrip_integrity(crypto_engine, temp_workspace):
    """Verifies that encrypt -> split -> reassemble -> decrypt recovers the exact original SHA-256 payload."""
    secret_content = b"Confidential Payload: B.Tech Capstone Project 2026 Round-Trip Integrity Test."
    source_file = os.path.join(temp_workspace, "secret.txt")
    with open(source_file, "wb") as f:
        f.write(secret_content)

    frag_dir = os.path.join(temp_workspace, "fragments")
    restored_dir = os.path.join(temp_workspace, "restored")
    password = "CorrectStrongPassword#2026"

    # Step 1: Encrypt and Partition
    _, metadata = crypto_engine.encrypt_and_split(source_file, password, output_dir=frag_dir)
    assert os.path.exists(os.path.join(frag_dir, "fragment_A_image.bin"))
    assert os.path.exists(os.path.join(frag_dir, "fragment_B_audio.bin"))
    assert os.path.exists(os.path.join(frag_dir, "fragment_C_text.bin"))

    # Step 2: Reassemble and Decrypt
    restored_file = crypto_engine.reassemble_and_decrypt(password, output_dir=frag_dir, restored_dir=restored_dir)

    with open(restored_file, "rb") as f:
        restored_data = f.read()

    # Exact bit-for-bit assertion
    assert restored_data == secret_content
    assert hashlib.sha256(restored_data).hexdigest() == metadata["original_sha256"]

# ----------------------------------------------------------------------
# TEST CASE 2: Tamper Detection (Active Steganalysis / Network Tamper Defense)
# ----------------------------------------------------------------------
def test_tamper_detection(crypto_engine, temp_workspace):
    """Verifies that flipping a single byte in any fragment raises a MAC integrity failure."""
    secret_content = b"Tamper Verification Test Data Block."
    source_file = os.path.join(temp_workspace, "tamper_test.txt")
    with open(source_file, "wb") as f:
        f.write(secret_content)

    frag_dir = os.path.join(temp_workspace, "fragments")
    password = "SecurePassword@123"

    crypto_engine.encrypt_and_split(source_file, password, output_dir=frag_dir)

    # Simulate network tampering: alter one single byte in Fragment A
    frag_a_path = os.path.join(frag_dir, "fragment_A_image.bin")
    with open(frag_a_path, "rb") as f:
        corrupted_bytes = bytearray(f.read())
    
    # Flip the first bit
    corrupted_bytes[0] ^= 0x01
    with open(frag_a_path, "wb") as f:
        f.write(corrupted_bytes)

    # Attempt reassembly; MUST raise ValueError due to AES-GCM tag mismatch
    with pytest.raises(ValueError, match="INTEGRITY_FAILURE"):
        crypto_engine.reassemble_and_decrypt(password, output_dir=frag_dir, restored_dir=temp_workspace)

# ----------------------------------------------------------------------
# TEST CASE 3: Wrong Password Rejection
# ----------------------------------------------------------------------
def test_wrong_password_rejection(crypto_engine, temp_workspace):
    """Verifies that an adversary with an incorrect passphrase is rejected by AES-GCM MAC validation."""
    secret_content = b"Top secret payload that cannot be unlocked with incorrect credentials."
    source_file = os.path.join(temp_workspace, "credentials_test.txt")
    with open(source_file, "wb") as f:
        f.write(secret_content)

    frag_dir = os.path.join(temp_workspace, "fragments")
    correct_pass = "ActualAdminKey$2026"
    wrong_pass = "AttackerGuessedKey$2026"

    crypto_engine.encrypt_and_split(source_file, correct_pass, output_dir=frag_dir)

    # Decryption attempt with wrong key MUST fail
    with pytest.raises(ValueError, match="INTEGRITY_FAILURE"):
        crypto_engine.reassemble_and_decrypt(wrong_pass, output_dir=frag_dir, restored_dir=temp_workspace)

# ----------------------------------------------------------------------
# TEST CASE 4: Edge-Case Handling — 0-Byte Empty File
# ----------------------------------------------------------------------
def test_empty_file_edge_case(crypto_engine, temp_workspace):
    """Verifies system handles 0-byte files without zero-division or indexing crashes."""
    source_file = os.path.join(temp_workspace, "empty.txt")
    open(source_file, "wb").close()  # create 0-byte file
    assert os.path.getsize(source_file) == 0

    frag_dir = os.path.join(temp_workspace, "fragments")
    restored_dir = os.path.join(temp_workspace, "restored")
    password = "EmptyFileTestKey!2026"

    crypto_engine.encrypt_and_split(source_file, password, output_dir=frag_dir)
    restored_file = crypto_engine.reassemble_and_decrypt(password, output_dir=frag_dir, restored_dir=restored_dir)

    assert os.path.getsize(restored_file) == 0

# ----------------------------------------------------------------------
# TEST CASE 5: High-Payload Stress Test (5 MB Binary Payload)
# ----------------------------------------------------------------------
def test_large_file_stress(crypto_engine, temp_workspace):
    """Stress-tests the deterministic 3-way slice and reassembly on a 5 MB binary payload."""
    payload_size = 5 * 1024 * 1024  # 5 Megabytes
    large_payload = os.urandom(payload_size)

    source_file = os.path.join(temp_workspace, "large_sample.bin")
    with open(source_file, "wb") as f:
        f.write(large_payload)

    frag_dir = os.path.join(temp_workspace, "fragments")
    restored_dir = os.path.join(temp_workspace, "restored")
    password = "StressTestPassword@2026"

    _, metadata = crypto_engine.encrypt_and_split(source_file, password, output_dir=frag_dir)
    
    # Assert fragments partitioned correctly across the 3 chunks
    assert sum(metadata["part_lengths"]) == payload_size + 16 + 2 + len(b"large_sample.bin")  # tag + name header + name

    restored_file = crypto_engine.reassemble_and_decrypt(password, output_dir=frag_dir, restored_dir=restored_dir)

    with open(restored_file, "rb") as f:
        restored_data = f.read()

    assert len(restored_data) == payload_size
    assert hashlib.sha256(restored_data).hexdigest() == metadata["original_sha256"]