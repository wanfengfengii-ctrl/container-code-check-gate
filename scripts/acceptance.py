#!/usr/bin/env python3
"""一次性验收脚本：对运行中的箱号校验 API 做端到端断言。

仅依赖 Python 标准库，可直接在 python:3.12-slim 镜像内运行。

用法：
    API_BASE_URL=http://api:8000 python scripts/acceptance.py

退出码 0 表示全部验收通过；任一断言失败即非零退出并打印失败原因。
脚本内置一份独立参考实现来复算校验位，不依赖被测服务自身的常量。
"""

from __future__ import annotations

import json
import os
import string
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
ENDPOINT = "/api/v1/container-numbers/verify"


# --------------------------------------------------------------- 参考实现


def _letter_table() -> dict[str, int]:
    table: dict[str, int] = {}
    nxt = 10
    for letter in string.ascii_uppercase:
        table[letter] = nxt
        nxt += 1
        if nxt % 11 == 0:
            nxt += 1
    return table


LETTERS = _letter_table()


def reference_check_digit(first_ten: str) -> tuple[int, int]:
    """返回 (加权和, 期望校验位)，与服务端实现相互独立。"""
    total = sum(
        (int(ch) if ch.isdigit() else LETTERS[ch]) * 2**i
        for i, ch in enumerate(first_ten)
    )
    remainder = total % 11
    return total, (0 if remainder == 10 else remainder)


def make_valid(prefix: str) -> str:
    _, check = reference_check_digit(prefix)
    return f"{prefix}{check}"


# ----------------------------------------------------------------- HTTP


def call_api(numbers: list[str]) -> tuple[int, object]:
    data = json.dumps({"container_numbers": numbers}).encode("utf-8")
    request = urllib.request.Request(
        BASE_URL + ENDPOINT,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def get_health() -> tuple[int, object]:
    with urllib.request.urlopen(BASE_URL + "/health", timeout=10) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


# ----------------------------------------------------------------- 断言


class Checks:
    def __init__(self) -> None:
        self.passed = 0
        self.failures: list[str] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed += 1
            print(f"  PASS  {name}")
        else:
            self.failures.append(f"{name} :: {detail}")
            print(f"  FAIL  {name} :: {detail}")

    def expect(self, name: str, actual: object, expected: object) -> None:
        self.check(
            name,
            actual == expected,
            f"expected={expected!r} actual={actual!r}",
        )


def run() -> int:
    checks = Checks()
    print(f"验收目标：{BASE_URL}")

    # 0. 健康检查
    try:
        status, body = get_health()
        checks.expect("0a GET /health 状态码 200", status, 200)
        checks.expect("0b GET /health 负载", body, {"status": "ok"})
    except Exception as exc:  # noqa: BLE001 - 验收脚本需打印全部异常
        checks.check("0 健康检查可达", False, repr(exc))
        return _report(checks)

    # 1. 结构合法且校验通过：ISO 6346 经典样例，结论可独立复算
    status, body = call_api(["CSQU3054383"])
    result = body["results"][0]
    ref_sum, ref_check = reference_check_digit("CSQU305438")
    checks.expect("1a 合法批次返回 200", status, 200)
    checks.expect("1b status=ok", body["status"], "ok")
    checks.expect("1c 加权和独立复算一致", result["weighted_sum"], ref_sum)
    checks.expect("1d 期望校验位独立复算一致", result["expected_check_digit"], ref_check)
    checks.expect("1e 实际校验位", result["actual_check_digit"], 3)
    checks.expect("1f passed=true", result["passed"], True)
    checks.expect(
        "1g 字段拆分",
        result["parts"],
        {
            "owner_code": "CSQ",
            "category_identifier": "U",
            "serial_number": "305438",
            "check_digit": "3",
        },
    )
    checks.expect("1h 原索引回显", result["index"], 0)

    # 2. 可解析但校验失败：结构 200，passed=false，期望/实际均可见
    status, body = call_api(["CSQU3054384"])
    result = body["results"][0]
    checks.expect("2a 校验位不符仍是可解析批次 200", status, 200)
    checks.expect("2b failed_count=1", body["failed_count"], 1)
    checks.expect("2c 期望校验位=3", result["expected_check_digit"], 3)
    checks.expect("2d 实际校验位=4", result["actual_check_digit"], 4)
    checks.expect("2e passed=false", result["passed"], False)

    # 3. 余数边界：余数 10 折叠为校验位 0
    status, body = call_api(["AAAU0000060"])
    result = body["results"][0]
    checks.expect("3a 余数10样例 200", status, 200)
    checks.expect("3b 加权和=3398", result["weighted_sum"], 3398)
    checks.expect("3c 余数10折叠为期望校验位0", result["expected_check_digit"], 0)
    checks.expect("3d passed=true", result["passed"], True)

    # 4. 第四位 U/J/Z 均可，末位由参考实现现场生成
    for category in "JZ":
        number = make_valid(f"ABC{category}123456")
        status, body = call_api([number])
        result = body["results"][0]
        checks.expect(f"4 类别码 {category} 通过 ({number})", result["passed"], True)

    # 5. 无法解析的批次：整批拒绝，定位最小输入索引与首个损坏位置
    status, body = call_api(["CSQU3054383", "csqu3054383"])
    checks.expect("5a 结构非法返回 422", status, 422)
    checks.expect("5b status=invalid_batch", body["status"], "invalid_batch")
    checks.expect("5c 最小非法索引=1", body["index"], 1)
    checks.expect("5d 错误码", body["error_code"], "not_uppercase_letter")
    checks.expect("5e 首个损坏位置=1", body["position"], 1)
    checks.expect("5f 原样回显非法输入", body["container_number"], "csqu3054383")
    checks.check("5g 不返回任何逐项结果", "results" not in body, str(body))

    # 6. 多个非法项时只报最小索引（第 0 项长度非法，第 1 项类别码非法）
    status, body = call_api(["!!", "CSQX3054383"])
    checks.expect("6a 最小索引 0 整批拒绝", body["index"], 0)
    checks.expect("6b 越界损坏位置=3", body["position"], 3)
    checks.expect("6c 错误码 invalid_length", body["error_code"], "invalid_length")

    # 7. 类别码仅 U/J/Z
    status, body = call_api(["CSQX3054383"])
    checks.expect("7a 非法类别码 422", status, 422)
    checks.expect("7b 损坏位置=4", body["position"], 4)

    # 8. 不做空白归一化
    status, body = call_api([" CSQU3054383"])
    checks.expect("8a 前导空白致长度非法 422", status, 422)
    checks.expect("8b 损坏位置=12", body["position"], 12)

    # 9. 混合批次保留原索引，通过/失败逐项给出
    numbers = [
        "CSQU3054383",  # pass
        "CSQU3054384",  # fail
        "AAAU0000060",  # pass
        "AAAU0000081",  # fail (前缀余 0，期望 0，实际 1)
    ]
    status, body = call_api(numbers)
    checks.expect("9a 混合批次 200", status, 200)
    checks.expect("9b passed_count=2", body["passed_count"], 2)
    checks.expect("9c failed_count=2", body["failed_count"], 2)
    checks.expect(
        "9d 逐项通过标志",
        [r["passed"] for r in body["results"]],
        [True, False, True, False],
    )
    checks.expect(
        "9e 原索引序列",
        [r["index"] for r in body["results"]],
        [0, 1, 2, 3],
    )

    # 10. 批量边界：1 与 100 接受，101 拒绝
    status, body = call_api(["CSQU3054383"])
    checks.expect("10a 单条批次 200", status, 200)
    status, body = call_api(["CSQU3054383"] * 100)
    checks.expect("10b 100 条批次 200", status, 200)
    checks.expect("10c 返回 100 项", len(body["results"]), 100)
    status, body = call_api(["CSQU3054383"] * 101)
    checks.expect("10d 101 条批次 422", status, 422)
    checks.check("10e 超限为请求形状错误(detail)", "detail" in body, str(body))

    # 11. 空批同样属于请求形状错误，而非业务 invalid_batch
    status, body = call_api([])
    checks.expect("11a 空批 422", status, 422)
    checks.check("11b 空批走 detail 形态", "detail" in body, str(body))

    return _report(checks)


def _report(checks: Checks) -> int:
    total = checks.passed + len(checks.failures)
    print(f"\n{checks.passed}/{total} 项验收通过")
    if checks.failures:
        print("失败项：")
        for failure in checks.failures:
            print(f"  - {failure}")
        print("\nACCEPTANCE FAILED")
        return 1
    print("ACCEPTANCE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(run())
