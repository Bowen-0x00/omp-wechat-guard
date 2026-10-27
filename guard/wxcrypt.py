"""企业微信回调消息加解密模块 (PKCS#7 + AES-256-CBC + SHA1 验签)."""

import base64
import struct
import hashlib
import time
from typing import Tuple, Optional
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from loguru import logger


class WXBizMsgCrypt:
    """企业微信消息加解密工具类."""

    def __init__(self, token: str, encoding_aes_key: str, corp_id: str):
        self.token = token.strip()
        self.corp_id = corp_id.strip()
        self.encoding_aes_key = encoding_aes_key.strip()
        # AESKey 长度为 43 字符，尾部补 '=' 还原为 32 字节 Base64 解码后的真实 AES Key
        self.key = base64.b64decode(self.encoding_aes_key + "=")
        self.iv = self.key[:16]

    def verify_signature(self, timestamp: str, nonce: str, echostr_or_encrypt: str, msg_signature: str) -> bool:
        """校验微信 SHA1 签名."""
        items = sorted([self.token, str(timestamp), str(nonce), str(echostr_or_encrypt)])
        computed = hashlib.sha1("".join(items).encode("utf-8")).hexdigest()
        return computed == msg_signature

    def decrypt(self, encrypted_b64: str) -> Tuple[str, str]:
        """解密微信密文.
        
        返回: (decrypted_xml_str, receive_id)
        """
        ciphertext = base64.b64decode(encrypted_b64)
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(self.iv))
        decryptor = cipher.decryptor()
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()

        # 去除 PKCS#7 填充 (填充字节长度由最后一个字节表示)
        pad_len = plaintext[-1]
        if pad_len < 1 or pad_len > 32:
            raise ValueError(f"非法的 PKCS7 填充长度: {pad_len}")
        plaintext = plaintext[:-pad_len]

        # 结构: 16字节随机数 + 4字节消息长度(网络大端序) + 消息XML + receive_id (corp_id)
        msg_len = struct.unpack(">I", plaintext[16:20])[0]
        msg = plaintext[20:20 + msg_len].decode("utf-8")
        receive_id = plaintext[20 + msg_len:].decode("utf-8")

        return msg, receive_id

    def decrypt_msg(self, msg_signature: str, timestamp: str, nonce: str, encrypt_xml_body: str) -> str:
        """从微信 POST 回调的 XML 中提取密文并完整解密."""
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(encrypt_xml_body)
            encrypt_elem = root.find("Encrypt")
            if encrypt_elem is None or not encrypt_elem.text:
                raise ValueError("未在 XML 中找到 Encrypt 节点")
            encrypt_b64 = encrypt_elem.text.strip()
        except Exception as e:
            logger.error(f"[WXCrypt] 解析回调 XML 失败: {e}")
            raise

        if not self.verify_signature(timestamp, nonce, encrypt_b64, msg_signature):
            raise ValueError("微信签名验证失败 (Signature mismatch)")

        decrypted_xml, _ = self.decrypt(encrypt_b64)
        return decrypted_xml

    def encrypt_msg(self, reply_xml: str, nonce: str, timestamp: Optional[str] = None) -> str:
        """加密回复消息为企业微信需要的 XML 结构."""
        import os
        timestamp = timestamp or str(int(time.time()))
        random_16 = os.urandom(16)
        raw_xml_bytes = reply_xml.encode("utf-8")

        # 结构: 16字节随机数 + 4字节长度(网络大端序) + 明文 + corp_id
        msg_len = struct.pack(">I", len(raw_xml_bytes))
        content = random_16 + msg_len + raw_xml_bytes + self.corp_id.encode("utf-8")

        # PKCS#7 补位到 32 字节倍数
        pad_len = 32 - (len(content) % 32)
        content += bytes([pad_len] * pad_len)

        cipher = Cipher(algorithms.AES(self.key), modes.CBC(self.iv))
        encryptor = cipher.encryptor()
        encrypted = encryptor.update(content) + encryptor.finalize()
        encrypt_b64 = base64.b64encode(encrypted).decode("utf-8")

        # 生成签名
        items = sorted([self.token, timestamp, str(nonce), encrypt_b64])
        signature = hashlib.sha1("".join(items).encode("utf-8")).hexdigest()

        return f"""<xml>
<Encrypt><![CDATA[{encrypt_b64}]]></Encrypt>
<MsgSignature><![CDATA[{signature}]]></MsgSignature>
<TimeStamp>{timestamp}</TimeStamp>
<Nonce><![CDATA[{nonce}]]></Nonce>
</xml>"""
