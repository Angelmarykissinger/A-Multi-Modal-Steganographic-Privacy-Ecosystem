import sys
import json
import os
import socket
import threading
import subprocess
import time
import uuid
import ipaddress
from module1_core import Module1LockAndSplit
from module2_embedding import Module2StegoEmbedding
from module3_p2p_dht import Module3P2PDHT

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
LOCK_FILE = os.path.join(ROOT_DIR, "seeder.lock")   # exists while the seeding process runs
STOP_FILE = os.path.join(ROOT_DIR, "seeder.stop")   # created by --stop to ask it to shut down
CARRIER_NAMES = ["stego_cover.png", "stego_cover.wav", "stego_cover.txt"]
FRAGMENT_FILES = ["fragment_A_image.bin", "fragment_B_audio.bin", "fragment_C_text.bin", "metadata.json"]
MAX_CARRIER_BYTES = 200 * 1024 * 1024   # refuse absurd sizes announced by a remote node
DEFAULT_TTL = 86400  # 24 hours; override with the STEGO_TTL_SECONDS environment variable (e.g. for demos)


def get_ttl() -> int:
    try:
        return int(os.environ.get("STEGO_TTL_SECONDS", DEFAULT_TTL))
    except ValueError:
        return DEFAULT_TTL


def _remove(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


def get_lan_ip() -> str:
    """The computer's address on the current Wi-Fi or hotspot network (no data is sent)."""
    ip = None
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        ip = probe.getsockname()[0]
    except OSError:
        ip = None
    finally:
        probe.close()
    if not ip or ip.startswith("127."):
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            ip = None
    if not ip or ip.startswith("127."):
        raise RuntimeError("No network address found. Connect to Wi-Fi or a hotspot first.")
    return ip


def read_secret_from_stdin() -> str:
    """The password is passed through standard input, so it never appears in the process list."""
    raw = sys.stdin.buffer.readline()
    secret = raw.decode("utf-8").rstrip("\r\n")
    if not secret:
        raise ValueError("No password received.")
    return secret


# ---------------------------------------------------------
# SEEDING PROCESS (--serve): serves 3 carriers, stops after the TTL or on --stop
# ---------------------------------------------------------
def run_server_daemon(out_dir: str, ttl_seconds: int, bind_host: str = "127.0.0.1"):
    files = {
        5001: os.path.join(out_dir, CARRIER_NAMES[0]),
        5002: os.path.join(out_dir, CARRIER_NAMES[1]),
        5003: os.path.join(out_dir, CARRIER_NAMES[2]),
    }

    _remove(STOP_FILE)
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))

    def serve_file(port, filepath):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind((bind_host, port))
            server.listen(5)
            while True:
                try:
                    conn, addr = server.accept()
                except OSError:
                    break                      # the listening socket itself failed
                try:
                    if os.path.exists(filepath):
                        with open(filepath, "rb") as f:
                            data = f.read()
                        conn.sendall(len(data).to_bytes(8, byteorder="big") + data)
                except Exception:
                    pass                       # a client that disconnects early must not stop the server
                finally:
                    conn.close()
        except Exception:
            pass
        finally:
            server.close()

    for port, path in files.items():
        threading.Thread(target=serve_file, args=(port, path), daemon=True).start()

    deadline = time.time() + ttl_seconds
    while time.time() < deadline and not os.path.exists(STOP_FILE):
        time.sleep(0.5)

    # Expired or stopped: withdraw the carriers, release the lock, and end the process (closes the ports)
    for name in CARRIER_NAMES:
        _remove(os.path.join(out_dir, name))
    _remove(STOP_FILE)
    _remove(LOCK_FILE)
    os._exit(0)


def stop_seeding(wait_seconds: float = 6.0) -> str:
    if not os.path.exists(LOCK_FILE):
        _remove(STOP_FILE)
        return "No seeding process was running."
    with open(STOP_FILE, "w") as f:
        f.write("stop")
    end = time.time() + wait_seconds
    while time.time() < end and os.path.exists(LOCK_FILE):
        time.sleep(0.2)
    if os.path.exists(LOCK_FILE):          # left behind by a crashed process
        _remove(LOCK_FILE)
        _remove(STOP_FILE)
        return "Removed a stale seeding lock."
    return "Seeding stopped and the carriers were withdrawn."


# ---------------------------------------------------------
# SENDER PIPELINE
# ---------------------------------------------------------
def run_sender_pipeline(payload_path: str, password: str, img_cover: str, aud_cover: str, txt_cover: str, lan: bool = False):
    try:
        ttl = get_ttl()
        # Local test: loopback only. LAN mode: listen on every network interface and advertise this computer's address.
        advertise_ip = get_lan_ip() if lan else "127.0.0.1"
        bind_host = "0.0.0.0" if lan else "127.0.0.1"
        stop_seeding()   # a previous session must not keep serving, or its timer would end the new one early

        crypto = Module1LockAndSplit()
        frag_dir = os.path.join(ROOT_DIR, "fragments")
        _, metadata = crypto.encrypt_and_split(payload_path, password, output_dir=frag_dir)

        stego = Module2StegoEmbedding()
        out_dir = os.path.join(ROOT_DIR, "stego_carriers")
        os.makedirs(out_dir, exist_ok=True)

        with open(os.path.join(frag_dir, "fragment_A_image.bin"), "rb") as f: fa = f.read()
        with open(os.path.join(frag_dir, "fragment_B_audio.bin"), "rb") as f: fb = f.read()
        with open(os.path.join(frag_dir, "fragment_C_text.bin"), "rb") as f: fc = f.read()

        stego.embed_in_image(fa, img_cover, os.path.join(out_dir, CARRIER_NAMES[0]))
        stego.embed_in_audio(fb, aud_cover, os.path.join(out_dir, CARRIER_NAMES[1]))
        stego.embed_in_text(fc, txt_cover, os.path.join(out_dir, CARRIER_NAMES[2]))

        p2p = Module3P2PDHT(ttl_seconds=ttl)
        base_magnet = p2p.generate_stego_magnet_uri(
            out_dir, "SESSION_" + uuid.uuid4().hex[:12], metadata_path=os.path.join(frag_dir, "metadata.json"))
        multi_node_magnet = base_magnet + f"&nodes={advertise_ip}:5001,{advertise_ip}:5002,{advertise_ip}:5003"

        # The fragments are now hidden inside the carriers, so the loose copies are deleted
        for name in FRAGMENT_FILES:
            _remove(os.path.join(frag_dir, name))

        # Seeding process: stops by itself when the TTL ends
        script_path = os.path.abspath(__file__)
        subprocess.Popen(
            [sys.executable, script_path, "--serve", out_dir, str(ttl), bind_host],
            creationflags=subprocess.DETACHED_PROCESS if os.name == 'nt' else 0,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        start_wait = time.time() + 5
        while time.time() < start_wait and not os.path.exists(LOCK_FILE):
            time.sleep(0.1)
        if not os.path.exists(LOCK_FILE):
            raise RuntimeError("The seeding process did not start.")

        expires_at = int(time.time()) + ttl
        expiry_text = time.strftime("%Y-%m-%d %H:%M", time.localtime(expires_at))
        return json.dumps({
            "status": "success",
            "magnet": multi_node_magnet,
            "expires_at": expires_at,
            "address": advertise_ip,
            "message": (f"Success! Multi-port nodes ({advertise_ip}, ports 5001-5003) are active"
                        f"{' for devices on this network' if lan else ' on this computer only'} "
                        f"and stop automatically at {expiry_text}.")
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ---------------------------------------------------------
# RECEIVER PIPELINE
# ---------------------------------------------------------
def recv_exact(sock, n):
    data = bytearray()
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            raise ConnectionError("Connection closed prematurely.")
        data.extend(packet)
    return bytes(data)


def run_receiver_pipeline(magnet_input: str, password: str):
    try:
        cleaned_input = magnet_input.strip()
        for char in ["'", '"', '\u2018', '\u2019', '\u201c', '\u201d']:
            while cleaned_input.startswith(char):
                cleaned_input = cleaned_input[1:]
            while cleaned_input.endswith(char):
                cleaned_input = cleaned_input[:-1]
        cleaned_input = cleaned_input.strip()

        if cleaned_input.startswith("stego://"):
            full_uri = cleaned_input
        else:
            full_uri = f"stego://dht?xt={cleaned_input}"

        if "&nodes=" not in full_uri:
            raise ValueError("Magnet link missing multi-node routing parameter (&nodes=).")

        # Check the link first: an expired link is rejected before any connection is made
        p2p = Module3P2PDHT()
        manifest = p2p.parse_stego_magnet_uri(full_uri)

        endpoints = full_uri.split("&nodes=")[1].split("&")[0].split(",")
        if len(endpoints) != 3:
            raise ValueError("The link must list exactly 3 nodes.")

        download_dir = os.path.join(ROOT_DIR, "network_received_carriers")
        os.makedirs(download_dir, exist_ok=True)
        carrier_files = [os.path.join(download_dir, n) for n in CARRIER_NAMES]
        for path in carrier_files:
            _remove(path)   # never compare against a download from an earlier session

        errors = []

        def fetch_node(endpoint, save_path):
            try:
                ip, port = endpoint.split(":")
                ipaddress.ip_address(ip)                      # must be a literal IP address
                if not (1024 <= int(port) <= 65535):
                    raise ValueError("port out of range")
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(20)
                s.connect((ip, int(port)))
                size_bytes = recv_exact(s, 8)
                file_size = int.from_bytes(size_bytes, byteorder="big")
                if file_size > MAX_CARRIER_BYTES:
                    raise ValueError("announced size is too large")
                received = 0
                with open(save_path, "wb") as f:
                    while received < file_size:
                        chunk = s.recv(min(65536, file_size - received))
                        if not chunk:
                            break
                        f.write(chunk)
                        received += len(chunk)
                s.close()
                if received != file_size:
                    errors.append(f"{endpoint}: incomplete download")
            except Exception as e:
                errors.append(f"{endpoint}: {e}")

        threads = []
        for i, ep in enumerate(endpoints):
            t = threading.Thread(target=fetch_node, args=(ep, carrier_files[i]))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        if errors:
            raise ConnectionError("Could not fetch all carriers (the link may have been withdrawn): " + "; ".join(errors))

        if p2p.compute_file_infohash(carrier_files[0]) != manifest["hashes"]["img"] or \
           p2p.compute_file_infohash(carrier_files[1]) != manifest["hashes"]["aud"] or \
           p2p.compute_file_infohash(carrier_files[2]) != manifest["hashes"]["txt"]:
            raise ValueError("INTEGRITY_FAILURE: Carrier Hash Mismatch!")

        stego = Module2StegoEmbedding()
        ext_a = stego.extract_from_image(carrier_files[0])
        ext_b = stego.extract_from_audio(carrier_files[1])
        ext_c = stego.extract_from_text(carrier_files[2])

        staging_dir = os.path.join(ROOT_DIR, "fragments")
        os.makedirs(staging_dir, exist_ok=True)
        with open(os.path.join(staging_dir, "fragment_A_image.bin"), "wb") as f: f.write(ext_a)
        with open(os.path.join(staging_dir, "fragment_B_audio.bin"), "wb") as f: f.write(ext_b)
        with open(os.path.join(staging_dir, "fragment_C_text.bin"), "wb") as f: f.write(ext_c)
        with open(os.path.join(staging_dir, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump(manifest["metadata"], f, indent=4)

        crypto = Module1LockAndSplit()
        restored_dir = os.path.join(ROOT_DIR, "real_receiver_output")
        try:
            restored_path = crypto.reassemble_and_decrypt(password, output_dir=staging_dir, restored_dir=restored_dir)
        finally:
            for name in FRAGMENT_FILES:           # do not leave the extracted ciphertext on disk
                _remove(os.path.join(staging_dir, name))

        return json.dumps({
            "status": "success",
            "message": f"Successfully swarmed 3 nodes, verified hashes, and restored file at '{restored_path}'!"
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "--send":
            try:
                lan = "--lan" in sys.argv[2:]
                args = [a for a in sys.argv[2:] if a != "--lan"]
                pwd = read_secret_from_stdin()
                print(run_sender_pipeline(args[0], pwd, args[1], args[2], args[3], lan=lan))
            except Exception as e:
                print(json.dumps({"status": "error", "message": str(e)}))
        elif cmd == "--receive":
            try:
                pwd = read_secret_from_stdin()
                print(run_receiver_pipeline(sys.argv[2], pwd))
            except Exception as e:
                print(json.dumps({"status": "error", "message": str(e)}))
        elif cmd == "--stop":
            print(json.dumps({"status": "success", "message": stop_seeding()}))
        elif cmd == "--serve":
            run_server_daemon(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_TTL,
                          sys.argv[4] if len(sys.argv) > 4 else "127.0.0.1")
