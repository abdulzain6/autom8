import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from aci.common import config


ENCRYPTION_KEY: bytes = bytes.fromhex(config.ENCRYPTION_SECRET_KEY)

def encrypt(plain_data: bytes) -> bytes:
    """
    Encrypts plaintext data using AES-256-GCM.

    Args:
        plain_data: The bytes to encrypt.

    Returns:
        The encrypted bytes, prefixed with the 12-byte nonce.
    """
    # A 12-byte (96-bit) nonce is recommended for AES-GCM.
    nonce = os.urandom(12)
    aesgcm = AESGCM(ENCRYPTION_KEY)
    cipher_text = aesgcm.encrypt(nonce, plain_data, associated_data=None)
    # Prepend the nonce to the ciphertext for use during decryption.
    return nonce + cipher_text

def decrypt(cipher_data: bytes) -> bytes:
    """
    Decrypts ciphertext using AES-256-GCM.
    It assumes the first 12 bytes of the cipher_data is the nonce.

    Args:
        cipher_data: The encrypted data, including the nonce prefix.

    Returns:
        The original decrypted bytes.
    """
    if len(cipher_data) < 13:
        raise ValueError("Invalid ciphertext length. Must include a 12-byte nonce.")
    
    # Extract the nonce and the actual ciphertext.
    nonce = cipher_data[:12]
    ciphertext = cipher_data[12:]
    
    aesgcm = AESGCM(ENCRYPTION_KEY)
    # Decrypt and return the plaintext.
    return aesgcm.decrypt(nonce, ciphertext, associated_data=None)

