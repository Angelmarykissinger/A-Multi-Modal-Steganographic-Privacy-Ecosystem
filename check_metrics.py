import numpy as np
import cv2
import scipy.io.wavfile as wav

print("--- PHASE 2 QUALITY METRICS (PSNR & SNR) ---")

# 1. IMAGE CARRIER PSNR (PNG)
cover_img = cv2.imread("cover.png")
stego_img = cv2.imread("stego_carriers/stego_cover.png")

if cover_img is not None and stego_img is not None:
    mse_img = np.mean((cover_img.astype(np.float64) - stego_img.astype(np.float64)) ** 2)
    psnr_img = float('inf') if mse_img == 0 else 20 * np.log10(255.0 / np.sqrt(mse_img))
    
    print(f"\n[1] IMAGE CARRIER (PNG):")
    print(f"    MSE:  {mse_img:.6f}")
    print(f"    PSNR: {psnr_img:.2f} dB")
    print(f"    Fidelity Status: {'EXCELLENT (>40 dB)' if psnr_img > 40 else 'POOR'}")

# 2. AUDIO CARRIER SNR (WAV)
rate_orig, cover_aud = wav.read("cover.wav")
rate_stego, stego_aud = wav.read("stego_carriers/stego_cover.wav")

if cover_aud is not None and stego_aud is not None:
    signal_power = np.sum(cover_aud.astype(np.float64) ** 2)
    noise_power = np.sum((cover_aud.astype(np.float64) - stego_aud.astype(np.float64)) ** 2)
    snr_aud = 10 * np.log10(signal_power / noise_power) if noise_power != 0 else float('inf')

    print(f"\n[2] AUDIO CARRIER (WAV):")
    print(f"    Signal Power: {signal_power:.2f}")
    print(f"    Noise Power:  {noise_power:.2f}")
    print(f"    SNR: {snr_aud:.2f} dB")
    print(f"    Fidelity Status: {'EXCELLENT (>40 dB)' if snr_aud > 40 else 'AUDIBLE DISTORTION'}")

# 3. TEXT CARRIER VISUAL IMPACT (TXT)
with open("cover.tex", "r", encoding="utf-8") as f: cover_txt = f.read()
with open("stego_carriers/stego_cover.txt", "r", encoding="utf-8") as f: stego_txt = f.read()

visible_cover_len = len(cover_txt)
visible_stego_len = len(stego_txt.replace("\u200B", "").replace("\u200C", ""))

print(f"\n[3] TEXT CARRIER (TXT):")
print(f"    Visible Cover Characters: {visible_cover_len}")
print(f"    Visible Stego Characters: {visible_stego_len}")
print(f"    Zero-Width Payload Size:  {len(stego_txt) - visible_stego_len} characters")
print(f"    Fidelity Status: {'EXCELLENT (100% Visually Identical)' if visible_cover_len == visible_stego_len else 'MODIFIED'}")