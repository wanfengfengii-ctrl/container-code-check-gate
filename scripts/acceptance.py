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
CORRECT_ENDPOINT = "/api/v1/container-numbers/correct"
EXPLAIN_ENDPOINT = "/api/v1/container-numbers/explain"
RECONCILE_ENDPOINT = "/api/v1/container-numbers/reconcile"


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


def reference_steps(first_ten: str) -> list[dict]:
    """独立参考实现：逐位置字符、映射值、二次幂权重与乘积。"""
    steps: list[dict] = []
    for index, char in enumerate(first_ten):
        value = int(char) if char.isdigit() else LETTERS[char]
        weight = 2**index
        steps.append(
            {
                "position": index + 1,
                "character": char,
                "value": value,
                "weight": weight,
                "product": value * weight,
            }
        )
    return steps


def make_valid(prefix: str) -> str:
    _, check = reference_check_digit(prefix)
    return f"{prefix}{check}"


def reference_structure_ok(number: str) -> bool:
    """独立结构判定：3 大写字母 + U/J/Z + 7 数字，恰 11 位。"""
    if len(number) != 11:
        return False
    return (
        all(c in string.ascii_uppercase for c in number[:3])
        and number[3] in "UJZ"
        and all(c in string.digits for c in number[4:])
    )


def reference_corrections(number: str) -> list[dict]:
    """枚举与原值汉明距离恰为 1 且结构、校验位均合法的候选，
    按（差异位置, 替换字符）稳定排序；与服务端实现相互独立。"""
    candidates: list[dict] = []
    for position in range(11):
        if position < 3:
            alphabet = string.ascii_uppercase
        elif position == 3:
            alphabet = "UJZ"
        else:
            alphabet = string.digits
        for replacement in sorted(alphabet):
            if replacement == number[position]:
                continue
            candidate = number[:position] + replacement + number[position + 1 :]
            if not reference_structure_ok(candidate):
                continue
            _, check = reference_check_digit(candidate[:10])
            if check == int(candidate[10]):
                candidates.append(
                    {
                        "position": position + 1,
                        "original_character": number[position],
                        "replacement_character": replacement,
                        "container_number": candidate,
                    }
                )
    return sorted(
        candidates, key=lambda c: (c["position"], c["replacement_character"])
    )


def hamming_distance(a: str, b: str) -> int:
    assert len(a) == len(b)
    return sum(x != y for x, y in zip(a, b))


def reference_reconcile(expected: list[str], onsite: list[str]) -> dict:
    """一次性清单核对的独立参考实现：逐箱号收集两侧原索引，按出现次序
    （第 k 次对第 k 次）对齐。与服务端 FIFO 队列实现刻意不同。

    返回 matched/missing/extra 三个列表；matched/missing 按预期索引排序，
    extra 按现场索引排序，对应契约规定的清单顺序。
    """
    epos: dict[str, list[int]] = {}
    opos: dict[str, list[int]] = {}
    for i, number in enumerate(expected):
        epos.setdefault(number, []).append(i)
    for i, number in enumerate(onsite):
        opos.setdefault(number, []).append(i)

    matched: list[dict] = []
    missing: list[dict] = []
    extra: list[dict] = []
    for number, ep in epos.items():
        op = opos.get(number, [])
        k = min(len(ep), len(op))
        matched.extend(
            {
                "container_number": number,
                "expected_index": e,
                "onsite_index": o,
            }
            for e, o in zip(ep[:k], op[:k])
        )
        missing.extend(
            {"container_number": number, "expected_index": i} for i in ep[k:]
        )
    for number, op in opos.items():
        ep = epos.get(number, [])
        extra.extend(
            {"container_number": number, "onsite_index": i} for i in op[len(ep) :]
        )
    matched.sort(key=lambda m: m["expected_index"])
    missing.sort(key=lambda m: m["expected_index"])
    extra.sort(key=lambda x: x["onsite_index"])
    return {"matched": matched, "missing": missing, "extra": extra}


# ----------------------------------------------------------------- HTTP


def call_verify_payload(payload: object) -> tuple[int, object]:
    """以任意 JSON 负载调用批量校验入口（用于开关与形状断言）。"""
    data = json.dumps(payload).encode("utf-8")
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


def call_api(numbers: list[str]) -> tuple[int, object]:
    return call_verify_payload({"container_numbers": numbers})


def call_correct(number: object) -> tuple[int, object]:
    data = json.dumps({"container_number": number}).encode("utf-8")
    request = urllib.request.Request(
        BASE_URL + CORRECT_ENDPOINT,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def call_explain_payload(payload: object) -> tuple[int, object]:
    """以任意 JSON 负载调用明细入口（用于请求形状断言）。"""
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        BASE_URL + EXPLAIN_ENDPOINT,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def call_explain(number: object) -> tuple[int, object]:
    return call_explain_payload({"container_number": number})


def call_reconcile_payload(payload: object) -> tuple[int, object]:
    """以任意 JSON 负载调用一次性清单核对入口（用于形状断言）。"""
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        BASE_URL + RECONCILE_ENDPOINT,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def call_reconcile(
    expected: object, onsite: object
) -> tuple[int, object]:
    return call_reconcile_payload(
        {
            "expected_container_numbers": expected,
            "onsite_container_numbers": onsite,
        }
    )


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

    # 12. 先校验后纠错：批量接口筛出未通过项，再提交纠错入口
    status, body = call_api(["CSQU3054383", "CSQU3054384"])
    checks.expect("12a 批量校验 200", status, 200)
    failed = [r["container_number"] for r in body["results"] if not r["passed"]]
    checks.expect("12b 未通过项即手抄偏差号", failed, ["CSQU3054384"])
    status, body = call_correct(failed[0])
    checks.expect("12c 纠错请求 200", status, 200)
    checks.expect("12d 歧义候选状态 multiple", body["status"], "multiple")
    expected = reference_corrections("CSQU3054384")
    checks.expect("12e 候选与独立汉明距离枚举一致", body["candidates"], expected)
    checks.expect("12f 候选数大于 1", body["candidate_count"] > 1, True)
    checks.expect(
        "12g candidate_count 与列表长度一致",
        body["candidate_count"],
        len(body["candidates"]),
    )
    checks.check(
        "12h 每个候选与原值汉明距离恰为 1",
        all(
            hamming_distance(c["container_number"], "CSQU3054384") == 1
            for c in body["candidates"]
        ),
        str(body["candidates"]),
    )
    checks.check(
        "12i 真实箱号在候选中",
        any(c["container_number"] == "CSQU3054383" for c in body["candidates"]),
        str(body["candidates"]),
    )
    checks.check(
        "12j 候选按(位置,替换字符)稳定排序",
        [(c["position"], c["replacement_character"]) for c in body["candidates"]]
        == sorted((c["position"], c["replacement_character"]) for c in body["candidates"]),
        str(body["candidates"]),
    )

    # 13. 唯一候选：类别码 X 非法，仅修成 U 后结构与校验位同时合法
    status, body = call_correct("CSQX3054383")
    checks.expect("13a 唯一候选请求 200", status, 200)
    checks.expect("13b 状态 unique", body["status"], "unique")
    checks.expect("13c 候选数 1", body["candidate_count"], 1)
    checks.expect(
        "13d 唯一候选内容",
        body["candidates"],
        [
            {
                "position": 4,
                "original_character": "X",
                "replacement_character": "U",
                "container_number": "CSQU3054383",
            }
        ],
    )
    checks.expect(
        "13e 与独立汉明距离枚举一致",
        body["candidates"],
        reference_corrections("CSQX3054383"),
    )

    # 14. 无候选：合法请求也返回 200 与 not_found
    status, body = call_correct("CSQX3054380")
    checks.expect("14a 无候选请求 200", status, 200)
    checks.expect("14b 状态 not_found", body["status"], "not_found")
    checks.expect("14c 候选数 0", body["candidate_count"], 0)
    checks.expect("14d 候选列表为空", body["candidates"], [])
    checks.expect(
        "14e 独立汉明距离枚举同样为空",
        reference_corrections("CSQX3054380"),
        [],
    )

    # 15. 纠错入口不做归一化、不混入原号自身
    status, body = call_correct("CSQU3054383")  # 原号本身合法
    checks.expect("15a 合法原号请求 200", status, 200)
    checks.check(
        "15b 原号自身不出现在候选中",
        all(
            c["container_number"] != "CSQU3054383" for c in body["candidates"]
        ),
        str(body["candidates"]),
    )
    checks.expect(
        "15c 与独立汉明距离枚举一致",
        body["candidates"],
        reference_corrections("CSQU3054383"),
    )
    status, body = call_correct("csqu3054383")  # 小写原样处理，不转大写
    checks.expect("15d 小写输入 200", status, 200)
    checks.expect("15e 小写原样回显", body["container_number"], "csqu3054383")
    checks.expect(
        "15f 小写候选与独立枚举一致",
        body["candidates"],
        reference_corrections("csqu3054383"),
    )

    # 16. 纠错入口请求形状校验：长度不符、类型错误均 422
    for label, payload in [
        ("16a 10 位", "CSQU305438"),
        ("16b 12 位", "CSQU30543834"),
        ("16c 前导空白(不 trim)", " CSQU3054383"),
    ]:
        status, body = call_correct(payload)
        checks.expect(f"{label} 422", status, 422)
        checks.check(f"{label} detail 形态", "detail" in body, str(body))
    status, body = call_correct(12345678901)
    checks.expect("16d 非字符串类型 422", status, 422)
    checks.check("16e 类型错误 detail 形态", "detail" in body, str(body))

    # 17. 旧接口回归：纠错引入后批量校验行为不变
    status, body = call_api(["CSQU3054383", "csqu3054383"])
    checks.expect("17a 旧接口非法批仍 422", status, 422)
    checks.expect("17b 旧接口最小非法索引", body["index"], 1)
    checks.expect("17c 旧接口错误码", body["error_code"], "not_uppercase_letter")
    status, body = call_api(["CSQU3054383"])
    checks.expect("17d 旧接口合法批仍 200", status, 200)
    checks.expect("17e 旧接口通过标志", body["results"][0]["passed"], True)

    # 18. 单箱计算明细：已知样例的十项乘积逐项核对，汇总独立复算
    status, body = call_explain("CSQU3054383")
    checks.expect("18a 明细请求 200", status, 200)
    checks.expect("18b status=ok", body["status"], "ok")
    checks.expect("18c 原样回显", body["container_number"], "CSQU3054383")
    checks.expect(
        "18d 十项步骤与独立参考一致",
        body["steps"],
        reference_steps("CSQU305438"),
    )
    checks.expect(
        "18e 已知样例十项乘积",
        [s["product"] for s in body["steps"]],
        [13, 60, 112, 256, 48, 0, 320, 512, 768, 4096],
    )
    checks.expect("18f 乘积合计=6185", body["weighted_sum"], 6185)
    checks.expect(
        "18g 合计等于十项乘积之和",
        body["weighted_sum"],
        sum(s["product"] for s in body["steps"]),
    )
    checks.expect("18h 取模结果=3", body["remainder"], 3)
    checks.expect("18i 期望校验位=3", body["expected_check_digit"], 3)
    checks.expect("18j 实际校验位=3", body["actual_check_digit"], 3)
    checks.expect("18k passed=true", body["passed"], True)

    # 19. 余数十折零边界：余数 10 原样呈现，期望校验位折叠为 0
    status, body = call_explain("AAAU0000060")
    checks.expect("19a 余数10样例 200", status, 200)
    checks.expect("19b 加权和=3398", body["weighted_sum"], 3398)
    checks.expect("19c 取模结果=10", body["remainder"], 10)
    checks.expect("19d 余数10折叠为期望校验位0", body["expected_check_digit"], 0)
    checks.expect("19e passed=true", body["passed"], True)
    status, body = call_explain("AAAU0000065")
    checks.expect("19f 余数10但末位错误仍 200", status, 200)
    checks.expect(
        "19g 期望0实际5",
        (body["expected_check_digit"], body["actual_check_digit"]),
        (0, 5),
    )
    checks.expect("19h passed=false", body["passed"], False)

    # 20. 结构损坏：沿用首个损坏位置与错误代码返回 422，不尝试纠正输入
    for label, number, code, position in [
        ("20a 小写不归一化", "csqu3054383", "not_uppercase_letter", 1),
        ("20b 非法类别码", "CSQX3054383", "invalid_category_identifier", 4),
        ("20c 末位非数字", "CSQU305438A", "not_digit", 11),
        ("20d 长度不足", "CSQU305438", "invalid_length", 11),
        ("20e 前导空白不trim", " CSQU3054383", "invalid_length", 12),
    ]:
        status, body = call_explain(number)
        checks.expect(f"{label} 422", status, 422)
        checks.expect(f"{label} status", body["status"], "invalid_container")
        checks.expect(f"{label} 错误码", body["error_code"], code)
        checks.expect(f"{label} 首个损坏位置", body["position"], position)
        checks.expect(f"{label} 原样回显", body["container_number"], number)
        checks.check(f"{label} 不返回明细", "steps" not in body, str(body))

    # 21. 明细入口请求形状：缺字段、类型错误、多余字段均 422 detail
    status, body = call_explain_payload({})
    checks.expect("21a 缺字段 422", status, 422)
    checks.check("21b 缺字段 detail 形态", "detail" in body, str(body))
    status, body = call_explain(12345678901)
    checks.expect("21c 非字符串类型 422", status, 422)
    checks.check("21d 类型错误 detail 形态", "detail" in body, str(body))
    status, body = call_explain_payload(
        {"container_number": "CSQU3054383", "normalize": True}
    )
    checks.expect("21e 多余字段 422", status, 422)
    checks.check("21f 多余字段 detail 形态", "detail" in body, str(body))

    # 22. 明细合计及结论始终等于原校验结果：批量校验后逐箱提交明细比对
    numbers = [
        "CSQU3054383",  # 通过
        "CSQU3054384",  # 末位不符
        "AAAU0000060",  # 余数 10 折 0
        "AAAU0000081",  # 余数 0 但末位不符
    ]
    status, body = call_api(numbers)
    checks.expect("22a 批量校验 200", status, 200)
    for result in body["results"]:
        number = result["container_number"]
        estatus, ebody = call_explain(number)
        checks.expect(f"22b {number} 明细 200", estatus, 200)
        steps_total = sum(s["product"] for s in ebody["steps"])
        checks.check(
            f"22c {number} 合计=十项乘积和=批量加权和",
            steps_total == ebody["weighted_sum"] == result["weighted_sum"],
            f"steps={steps_total} explain={ebody['weighted_sum']} "
            f"verify={result['weighted_sum']}",
        )
        checks.check(
            f"22d {number} 期望/实际/结论与批量一致",
            (
                ebody["expected_check_digit"],
                ebody["actual_check_digit"],
                ebody["passed"],
            )
            == (
                result["expected_check_digit"],
                result["actual_check_digit"],
                result["passed"],
            ),
            f"explain=({ebody['expected_check_digit']}, "
            f"{ebody['actual_check_digit']}, {ebody['passed']}) "
            f"verify=({result['expected_check_digit']}, "
            f"{result['actual_check_digit']}, {result['passed']})",
        )
        checks.expect(
            f"22e {number} 取模结果", ebody["remainder"], result["weighted_sum"] % 11
        )

    # 23. 旧接口回归：明细入口引入后批量校验与单箱纠错行为不变
    status, body = call_api(["CSQU3054383", "CSQU3054384"])
    checks.expect("23a 批量校验仍 200", status, 200)
    checks.expect(
        "23b 逐项通过标志不变",
        [r["passed"] for r in body["results"]],
        [True, False],
    )
    status, body = call_correct("CSQU3054384")
    checks.expect("23c 纠错入口仍 200", status, 200)
    checks.expect("23d 纠错状态仍 multiple", body["status"], "multiple")
    checks.expect(
        "23e 纠错候选与独立枚举一致",
        body["candidates"],
        reference_corrections("CSQU3054384"),
    )

    # 24. 未配对代理字符：JSON \uXXXX 转义可构造，首位代理字符不属于 A-Z。
    # 批量校验与明细入口必须在位置 1 报结构错误，纠错入口照常返回候选；
    # 响应中的原样回显经 \uXXXX 转义传输，解析后无损还原。
    surrogate_number = "\ud800SQU3054383"  # 恰 11 位，首位为未配对高代理字符
    status, body = call_api(["CSQU3054383", surrogate_number])
    checks.expect("24a 代理字符批次返回 422", status, 422)
    checks.expect("24b status=invalid_batch", body["status"], "invalid_batch")
    checks.expect("24c 最小非法索引=1", body["index"], 1)
    checks.expect("24d 错误码", body["error_code"], "not_uppercase_letter")
    checks.expect("24e 首个损坏位置=1", body["position"], 1)
    checks.expect("24f 代理字符原样回显", body["container_number"], surrogate_number)
    checks.check("24g 不返回任何逐项结果", "results" not in body, str(body))

    status, body = call_explain(surrogate_number)
    checks.expect("24h 代理字符明细返回 422", status, 422)
    checks.expect("24i status=invalid_container", body["status"], "invalid_container")
    checks.expect("24j 错误码", body["error_code"], "not_uppercase_letter")
    checks.expect("24k 首个损坏位置=1", body["position"], 1)
    checks.expect("24l 代理字符原样回显", body["container_number"], surrogate_number)
    checks.check("24m 不返回明细", "steps" not in body, str(body))

    status, body = call_correct(surrogate_number)
    checks.expect("24n 代理字符纠错返回 200", status, 200)
    checks.expect("24o 纠错状态 multiple", body["status"], "multiple")
    checks.expect("24p 代理字符原样回显", body["container_number"], surrogate_number)
    checks.expect(
        "24q 候选与独立汉明距离枚举一致",
        body["candidates"],
        reference_corrections(surrogate_number),
    )
    checks.expect(
        "24r candidate_count 与列表长度一致",
        body["candidate_count"],
        len(body["candidates"]),
    )
    checks.check(
        "24s 位置1候选的原字符即代理字符",
        any(
            c["position"] == 1 and c["original_character"] == "\ud800"
            for c in body["candidates"]
        ),
        str(body["candidates"]),
    )

    # 25. 请求形状错误的 detail 回显代理字符输入时仍正常返回 422
    status, body = call_correct(surrogate_number + "X")  # 12 位，超长
    checks.expect("25a 超长代理字符输入 422", status, 422)
    checks.check("25b 请求形状错误 detail 形态", "detail" in body, str(body))

    # 26. 箱主汇总开关：交错箱主批次一次调用同时取得逐箱结论与班组统计，
    # 独立计数复核各组之和等于原批次总数且失败数一致。
    numbers = [
        "CSQU3054383",  # CSQ 通过
        "AAAU0000060",  # AAA 通过
        "CSQU3054384",  # CSQ 未通过
        "MSCU6355890",  # MSC（结论由独立复算判定）
        "AAAU0000081",  # AAA 未通过
        "CSQU3054383",  # CSQ 通过（重复箱号）
    ]
    status, body = call_verify_payload(
        {"container_numbers": numbers, "include_owner_summary": True}
    )
    checks.expect("26a 开启开关的批次 200", status, 200)
    checks.expect(
        "26b 逐箱结论与独立复算一致",
        [r["passed"] for r in body["results"]],
        [reference_check_digit(n[:10])[1] == int(n[10]) for n in numbers],
    )
    summary = body["owner_summary"]
    checks.expect(
        "26c 按箱主首次出现顺序",
        [g["owner_code"] for g in summary],
        ["CSQ", "AAA", "MSC"],
    )
    # 独立归组参考：先取首次出现顺序，再逐箱主整批重扫计数。
    owners = list(dict.fromkeys(n[:3] for n in numbers))
    expected_summary = [
        {
            "owner_code": owner,
            "total": sum(1 for n in numbers if n[:3] == owner),
            "passed": sum(
                1
                for n in numbers
                if n[:3] == owner
                and reference_check_digit(n[:10])[1] == int(n[10])
            ),
            "failed": sum(
                1
                for n in numbers
                if n[:3] == owner
                and reference_check_digit(n[:10])[1] != int(n[10])
            ),
        }
        for owner in owners
    ]
    checks.expect("26d 各组计数与独立归组一致", summary, expected_summary)
    checks.expect(
        "26e 各组总数之和等于原批次总数",
        sum(g["total"] for g in summary),
        len(numbers),
    )
    checks.expect(
        "26f 各组失败数之和等于整批失败数",
        sum(g["failed"] for g in summary),
        body["failed_count"],
    )
    checks.expect(
        "26g 各组通过数之和等于整批通过数",
        sum(g["passed"] for g in summary),
        body["passed_count"],
    )
    checks.expect("26h 重复箱主只形成一项", len(summary), 3)

    # 27. 开关省略或为假：响应与旧版逐字段一致，原有客户端无需处理新字段
    status, body = call_api(["CSQU3054383", "CSQU3054384"])
    checks.expect("27a 省略开关的批次 200", status, 200)
    checks.check(
        "27b 省略开关无 owner_summary 字段",
        "owner_summary" not in body,
        str(body),
    )
    status, body_off = call_verify_payload(
        {
            "container_numbers": ["CSQU3054383", "CSQU3054384"],
            "include_owner_summary": False,
        }
    )
    checks.expect("27c 开关为假仍 200", status, 200)
    checks.expect("27d 开关为假与省略时逐字段一致", body_off, body)

    # 28. 开关开启时结构非法批仍整批拒绝且不产生汇总；开关类型错误走 detail
    status, body = call_verify_payload(
        {
            "container_numbers": ["CSQU3054383", "csqu3054383"],
            "include_owner_summary": True,
        }
    )
    checks.expect("28a 开关开启的非法批仍 422", status, 422)
    checks.expect("28b status=invalid_batch", body["status"], "invalid_batch")
    checks.expect("28c 最小非法索引=1", body["index"], 1)
    checks.check("28d 不产生汇总", "owner_summary" not in body, str(body))
    checks.check("28e 不返回逐项结果", "results" not in body, str(body))
    status, body = call_verify_payload(
        {"container_numbers": ["CSQU3054383"], "include_owner_summary": "yes"}
    )
    checks.expect("28f 开关类型错误 422", status, 422)
    checks.check("28g 开关类型错误 detail 形态", "detail" in body, str(body))

    # ============================================ 一次性清单核对（换班交接）
    # 用独立参考实现现场生成合法箱号，不写死被测服务常量。
    RA = make_valid("CSQU305438")  # CSQU3054383
    RB = make_valid("AAAU000006")  # AAAU0000060
    RC = make_valid("BBBU000000")  # BBBU0000000
    RD = make_valid("ABCU123456")  # ABCU1234560
    RE_ = make_valid("MSCU635589")  # MSCU6355895
    RA_BAD = "CSQU3054384"  # 结构合法但校验位不符（期望 3，实际 4）

    def assert_conservation(prefix: str, body: dict, n_exp: int, n_on: int) -> None:
        """配对数量守恒：计数与列表长度、两侧清单长度一致；原索引不重不漏。"""
        checks.expect(
            f"{prefix} matched_count 与列表一致",
            body["matched_count"],
            len(body["matched"]),
        )
        checks.expect(
            f"{prefix} missing_count 与列表一致",
            body["missing_count"],
            len(body["missing"]),
        )
        checks.expect(
            f"{prefix} extra_count 与列表一致",
            body["extra_count"],
            len(body["extra"]),
        )
        checks.expect(
            f"{prefix} 配对+缺少=预期总数",
            body["matched_count"] + body["missing_count"],
            n_exp,
        )
        checks.expect(
            f"{prefix} 配对+多出=现场总数",
            body["matched_count"] + body["extra_count"],
            n_on,
        )
        exp_idx = sorted(
            [m["expected_index"] for m in body["matched"]]
            + [m["expected_index"] for m in body["missing"]]
        )
        on_idx = sorted(
            [m["onsite_index"] for m in body["matched"]]
            + [x["onsite_index"] for x in body["extra"]]
        )
        checks.expect(f"{prefix} 预期原索引不重不漏", exp_idx, list(range(n_exp)))
        checks.expect(f"{prefix} 现场原索引不重不漏", on_idx, list(range(n_on)))

    # 29. 场景一：完全一致（乱序、含重复）——全部配对，无差异，原索引正确。
    expected = [RA, RB, RA, RC]
    onsite = [RC, RA, RB, RA]
    status, body = call_reconcile(expected, onsite)
    ref = reference_reconcile(expected, onsite)
    checks.expect("29a 完全一致(乱序) 200", status, 200)
    checks.expect("29b status=ok", body["status"], "ok")
    checks.expect(
        "29c 计数 4/0/0",
        (body["matched_count"], body["missing_count"], body["extra_count"]),
        (4, 0, 0),
    )
    checks.expect("29d 配对与独立参考一致", body["matched"], ref["matched"])
    checks.expect("29e 缺少为空", body["missing"], [])
    checks.expect("29f 多出为空", body["extra"], [])
    assert_conservation("29g", body, len(expected), len(onsite))

    # 30. 场景二：单侧缺失与现场多出并存（乱序），顺序与原索引正确。
    expected = [RA, RB, RC]
    onsite = [RD, RA, RB]  # 缺 RC，多 RD
    status, body = call_reconcile(expected, onsite)
    ref = reference_reconcile(expected, onsite)
    checks.expect("30a 单侧缺失/多出 200", status, 200)
    checks.expect(
        "30b 计数 2/1/1",
        (body["matched_count"], body["missing_count"], body["extra_count"]),
        (2, 1, 1),
    )
    checks.expect("30c 配对与独立参考一致", body["matched"], ref["matched"])
    checks.expect("30d 缺少与独立参考一致", body["missing"], ref["missing"])
    checks.expect("30e 多出与独立参考一致", body["extra"], ref["extra"])
    checks.expect(
        "30f 缺少项按预期顺序带原索引",
        body["missing"],
        [{"container_number": RC, "expected_index": 2}],
    )
    checks.expect(
        "30g 多出项按现场顺序带原索引",
        body["extra"],
        [{"container_number": RD, "onsite_index": 0}],
    )
    assert_conservation("30h", body, len(expected), len(onsite))

    # 31. 场景三：重复次数不等——三次对两次时只有最后一次归入差异。
    # 预期 3 次 RA、现场 2 次：预期最后一次（索引 3）为缺少。
    expected = [RA, RB, RA, RA]
    onsite = [RA, RA, RB]
    status, body = call_reconcile(expected, onsite)
    checks.expect("31a 预期3次现场2次 200", status, 200)
    checks.expect(
        "31b 计数 3/1/0",
        (body["matched_count"], body["missing_count"], body["extra_count"]),
        (3, 1, 0),
    )
    checks.expect("31c 与独立参考一致", body["matched"], reference_reconcile(expected, onsite)["matched"])
    checks.expect(
        "31d 仅最后一次(预期索引3)缺少",
        body["missing"],
        [{"container_number": RA, "expected_index": 3}],
    )
    assert_conservation("31e", body, len(expected), len(onsite))
    # 现场 3 次、预期 2 次：现场最后一次（索引 2）为多出。
    expected = [RB, RA]
    onsite = [RA, RB, RA]
    status, body = call_reconcile(expected, onsite)
    checks.expect(
        "31f 计数 2/0/1",
        (body["matched_count"], body["missing_count"], body["extra_count"]),
        (2, 0, 1),
    )
    checks.expect(
        "31g 仅最后一次(现场索引2)多出",
        body["extra"],
        [{"container_number": RA, "onsite_index": 2}],
    )
    assert_conservation("31h", body, len(expected), len(onsite))

    # 32. 乱序及重复组合样例：与独立参考逐字段一致，配对数量守恒。
    shuffled_samples = [
        ([RA, RB, RA, RC, RB], [RB, RA, RC, RA, RB]),
        ([RA, RA, RB], [RB, RA, RA]),
        ([RC, RA, RD, RB, RE_], [RE_, RB, RD, RA, RD]),
        ([RA, RB, RC, RA], [RA, RA, RB, RC]),
        ([RE_, RE_, RD, RE_, RD], [RD, RE_, RD, RE_, RE_]),
        ([RA, RB, RC, RD, RE_], [RE_, RD, RC, RB, RA]),
        ([RA, RA, RA], [RA, RA]),
        ([RA, RA], [RA, RA, RA]),
    ]
    for si, (expected, onsite) in enumerate(shuffled_samples):
        status, body = call_reconcile(expected, onsite)
        prefix = f"32.{si}"
        checks.expect(f"{prefix}a 样例 200", status, 200)
        ref = reference_reconcile(expected, onsite)
        checks.expect(f"{prefix}b 配对与独立参考一致", body["matched"], ref["matched"])
        checks.expect(f"{prefix}c 缺少与独立参考一致", body["missing"], ref["missing"])
        checks.expect(f"{prefix}d 多出与独立参考一致", body["extra"], ref["extra"])
        assert_conservation(prefix + "e", body, len(expected), len(onsite))

    # 33. 场景四：任一清单含结构非法或校验失败箱号——整次核对拒绝，
    # 明确清单来源、最小输入索引与原校验结论。
    status, body = call_reconcile([RA, "csqu3054383"], [RA])
    checks.expect("33a 预期清单结构非法 422", status, 422)
    checks.expect("33b status=invalid_item", body["status"], "invalid_item")
    checks.expect("33c 来源 expected", body["list_source"], "expected")
    checks.expect("33d 最小输入索引=1", body["index"], 1)
    checks.expect("33e 原样回显", body["container_number"], "csqu3054383")
    checks.expect("33f 原校验结论-结构错误码", body["error_code"], "not_uppercase_letter")
    checks.expect("33g 原校验结论-首个损坏位置", body["position"], 1)
    checks.expect("33h 结构非法时校验位为空", body["expected_check_digit"], None)
    checks.expect("33i 结构非法时实际校验位为空", body["actual_check_digit"], None)
    checks.expect("33j passed=false", body["passed"], False)
    checks.check("33k 不返回任何配对结果", "matched" not in body, str(body))

    status, body = call_reconcile([RA], [RA, "CSQX3054383"])
    checks.expect("33l 现场清单结构非法 422", status, 422)
    checks.expect("33m 来源 onsite", body["list_source"], "onsite")
    checks.expect("33n 最小输入索引=1", body["index"], 1)
    checks.expect("33o 错误码", body["error_code"], "invalid_category_identifier")
    checks.expect("33p 损坏位置=4", body["position"], 4)

    # 校验位不符（结构合法）：结构字段为空，原校验结论以期望/实际校验位给出。
    status, body = call_reconcile([RA], [RA_BAD])
    checks.expect("33q 校验位不符 422", status, 422)
    checks.expect("33r 来源 onsite", body["list_source"], "onsite")
    checks.expect("33s 最小输入索引=0", body["index"], 0)
    checks.expect("33t 结构字段为空", body["error_code"], None)
    checks.expect("33u 原结论期望校验位=3", body["expected_check_digit"], 3)
    checks.expect("33v 原结论实际校验位=4", body["actual_check_digit"], 4)
    checks.expect("33w passed=false", body["passed"], False)

    # 两侧均无效时先报预期清单（即使现场侧索引更小）；清单内取最小索引。
    status, body = call_reconcile([RA, "CSQX3054383"], ["!!", RA])
    checks.expect("33x 先查预期清单", body["list_source"], "expected")
    checks.expect("33y 预期侧最小索引=1", body["index"], 1)
    status, body = call_reconcile(["!!", "csqu3054383", RA], [RA])
    checks.expect("33z 清单内最小索引=0", body["index"], 0)
    checks.expect("33aa 越界位置=3", body["position"], 3)

    # 34. 请求形状：空清单、超 100、类型错误、缺字段、多余字段 -> 标准 422 detail；
    # 边界 1 与 100 接受。
    for label, payload in [
        ("34a 预期空清单", {"expected_container_numbers": [], "onsite_container_numbers": [RA]}),
        ("34b 现场空清单", {"expected_container_numbers": [RA], "onsite_container_numbers": []}),
        ("34c 预期超100", {"expected_container_numbers": [RA] * 101, "onsite_container_numbers": [RA]}),
        ("34d 现场超100", {"expected_container_numbers": [RA], "onsite_container_numbers": [RA] * 101}),
        ("34e 元素类型错误", {"expected_container_numbers": [12345678901], "onsite_container_numbers": [RA]}),
        ("34f 缺现场字段", {"expected_container_numbers": [RA]}),
        (
            "34g 多余字段",
            {
                "expected_container_numbers": [RA],
                "onsite_container_numbers": [RA],
                "normalize": True,
            },
        ),
    ]:
        status, body = call_reconcile_payload(payload)
        checks.expect(f"{label} 422", status, 422)
        checks.check(f"{label} detail 形态", "detail" in body, str(body))

    status, body = call_reconcile([RA], [RA])
    checks.expect("34h 单侧1项 200", status, 200)
    checks.expect("34i 配对1项", body["matched_count"], 1)
    status, body = call_reconcile([RA] * 100, [RA] * 100)
    checks.expect("34j 单侧100项 200", status, 200)
    checks.expect("34k 配对100项", body["matched_count"], 100)

    # 35. 旧接口回归：核对入口引入后批量校验、纠错、明细路径与响应不变。
    status, body = call_api(["CSQU3054383", "CSQU3054384"])
    checks.expect("35a 批量校验仍 200", status, 200)
    checks.expect(
        "35b 逐项通过标志不变",
        [r["passed"] for r in body["results"]],
        [True, False],
    )
    status, body = call_api(["CSQU3054383", "csqu3054383"])
    checks.expect("35c 批量非法批仍 422", status, 422)
    checks.expect("35d 旧接口最小非法索引", body["index"], 1)
    status, body = call_correct("CSQX3054383")
    checks.expect("35e 纠错入口仍 200", status, 200)
    checks.expect("35f 纠错状态仍 unique", body["status"], "unique")
    status, body = call_explain("CSQU3054383")
    checks.expect("35g 明细入口仍 200", status, 200)
    checks.expect("35h 明细结论仍 passed", body["passed"], True)

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
