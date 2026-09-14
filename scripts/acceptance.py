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
