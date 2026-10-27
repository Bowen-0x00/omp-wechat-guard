"""企业微信加解密与签名验证单元测试."""

import pytest
from guard.wxcrypt import WXBizMsgCrypt


def test_wxcrypt_roundtrip():
    token = "sample_test_token_12345"
    encoding_aes_key = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"
    corp_id = "wx_mock_corp_id_123456"

    crypt = WXBizMsgCrypt(token, encoding_aes_key, corp_id)

    raw_xml = "<xml><ToUserName><![CDATA[toUser]]></ToUserName><FromUserName><![CDATA[fromUser]]></FromUserName><CreateTime>1348831860</CreateTime><MsgType><![CDATA[text]]></MsgType><Content><![CDATA[/status]]></Content><MsgId>1234567890123456</MsgId><AgentID>1000006</AgentID></xml>"
    nonce = "132456"
    timestamp = "1409659813"

    # 1. 加密
    encrypted_xml = crypt.encrypt_msg(raw_xml, nonce, timestamp)
    assert "<Encrypt>" in encrypted_xml
    assert "<MsgSignature>" in encrypted_xml

    # 提取签名
    import xml.etree.ElementTree as ET
    root = ET.fromstring(encrypted_xml)
    signature = root.findtext("MsgSignature")

    # 2. 解密
    decrypted = crypt.decrypt_msg(signature, timestamp, nonce, encrypted_xml)
    assert "<Content><![CDATA[/status]]></Content>" in decrypted
    assert "<AgentID>1000006</AgentID>" in decrypted
