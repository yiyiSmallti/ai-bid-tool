"""Outbound masking rules on synthetic strings, no database or vendor.

Failure modes identified before the change:
* Over-masking: a label word inside ordinary tender wording ("刷身份证登录",
  "管理员账号", "联系人管理", "预算管理", "电话支持") hides the rest of the clause.
* English label substrings ("Intel" containing "tel") act as labels.
* Under-masking: tightening labels drops spaced, hyphenated or partly starred
  numbers, IBANs, written-out amounts without a separator, or labelled names.
* Disabled masking stops reporting detections.
"""

import pytest
from app.services.redaction import redact

KEPT = [
    "支持刷身份证登录，读取身份证信息",
    "管理员账号权限管理；账号密码登录；账号数量不少于 1000 个",
    "支持联系人管理和分组",
    "预算管理模块，价格合理；单价、总价均含税",
    "Intel 8358 处理器",
    "提供 7×24 小时电话支持，电话 5 分钟内响应",
    "contact the vendor for support; price list display",
]

MASKED = [
    ("身份证号码：110101 19900307 1234", "110101"),
    ("身份证号 110101********1234", "1234"),
    ("账号：6222 0212 3456 7890", "6222"),
    ("收款账号 62220212345678", "62220212345678"),
    ("IBAN: GB82 WEST 1234 5698 7654 32", "GB82"),
    ("联系人：张三", "张三"),
    ("Contact: Synthetic Person; Phone: 13800138000;", "Synthetic Person"),
    ("电话：010-12345678 转 801", "12345678"),
    ("Tel. +1 (555) 123-4567", "123-4567"),
    ("报价：人民币壹拾万元整", "壹拾万"),
    ("投标总价为人民币壹拾万元整", "壹拾万"),
    ("报价 98765.43", "98765.43"),
    ("合同价 1,234,567", "1,234,567"),
    ("价格：见附件", "见附件"),
]


@pytest.mark.parametrize("text", KEPT)
def test_label_words_in_ordinary_wording_stay_readable(text):
    sent, counts = redact(text, True)
    assert sent == text and not any(counts.values())


@pytest.mark.parametrize(("text", "secret"), MASKED)
def test_labelled_values_are_masked(text, secret):
    sent, counts = redact(text, True)
    assert secret not in sent and "[REDACTED_" in sent and any(counts.values())
    unmasked, disabled = redact(text, False)
    assert unmasked == text and disabled == counts
