"""批量校验 API 的端到端测试（经 FastAPI TestClient，不打网络栈）。

关键区分：
* 请求本身不符 schema（空批、超过 100 项、类型错误、多余字段）->
  FastAPI/Pydantic 标准 422（detail 数组）；
* 请求形状正确但某个箱号结构非法 -> 业务负载 status=invalid_batch，
  整批 422 拒绝并给出最小输入索引与首个损坏位置；
* 结构全部合法 -> 200，逐项可复算结论，校验位不符的箱仅 passed=false。
"""

from __future__ import annotations

import json
import string
from typing import Any

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _reference_weighted_sum(first_ten: str) -> int:
    """测试侧独立参考实现，用于复核服务端返回的加权和。"""
    values: dict[str, int] = {}
    nxt = 10
    for letter in string.ascii_uppercase:
        values[letter] = nxt
        nxt += 1
        if nxt % 11 == 0:
            nxt += 1
    total = 0
    for position, char in enumerate(first_ten):
        value = int(char) if char.isdigit() else values[char]
        total += value * 2**position
    return total


VALID_ENDPOINT = "/api/v1/container-numbers/verify"
CORRECT_ENDPOINT = "/api/v1/container-numbers/correct"
EXPLAIN_ENDPOINT = "/api/v1/container-numbers/explain"
RECONCILE_ENDPOINT = "/api/v1/container-numbers/reconcile"


def _reference_steps(first_ten: str) -> list[dict[str, Any]]:
    """测试侧独立参考实现：逐位置字符、映射值、二次幂权重与乘积。"""
    values: dict[str, int] = {}
    nxt = 10
    for letter in string.ascii_uppercase:
        values[letter] = nxt
        nxt += 1
        if nxt % 11 == 0:
            nxt += 1
    steps: list[dict[str, Any]] = []
    for index, char in enumerate(first_ten):
        value = int(char) if char.isdigit() else values[char]
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


def _hamming_distance(a: str, b: str) -> int:
    """两等长串的差异位置数。"""
    assert len(a) == len(b)
    return sum(x != y for x, y in zip(a, b))


def _reference_structure_ok(number: str) -> bool:
    """测试侧独立结构判定：3 字母 + U/J/Z + 7 数字，恰 11 位。"""
    if len(number) != 11:
        return False
    return (
        all(c in string.ascii_uppercase for c in number[:3])
        and number[3] in "UJZ"
        and all(c in string.digits for c in number[4:])
    )


def _reference_corrections(number: str) -> list[dict[str, Any]]:
    """测试侧独立参考实现：枚举与原值汉明距离恰为 1 的全部串，
    用独立的结构判定与校验位复算筛选，按（差异位置, 替换字符）稳定排序。"""
    candidates: list[dict[str, Any]] = []
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
            if not _reference_structure_ok(candidate):
                continue
            remainder = _reference_weighted_sum(candidate[:10]) % 11
            expected = 0 if remainder == 10 else remainder
            if expected == int(candidate[10]):
                candidates.append(
                    {
                        "position": position + 1,
                        "original_character": number[position],
                        "replacement_character": replacement,
                        "container_number": candidate,
                    }
                )
    return sorted(
        candidates,
        key=lambda c: (c["position"], c["replacement_character"]),
    )


# ---------------------------------------------------------------- 基础路由


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------- 校验通过


def test_single_valid_number_full_recomputable_payload() -> None:
    response = client.post(VALID_ENDPOINT, json={"container_numbers": ["CSQU3054383"]})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["count"] == 1
    assert body["passed_count"] == 1
    assert body["failed_count"] == 0

    result = body["results"][0]
    assert result["index"] == 0
    assert result["container_number"] == "CSQU3054383"
    assert result["parts"] == {
        "owner_code": "CSQ",
        "category_identifier": "U",
        "serial_number": "305438",
        "check_digit": "3",
    }
    # 加权和由测试侧独立参考实现复算，而非抄服务端常量。
    assert result["weighted_sum"] == _reference_weighted_sum("CSQU305438") == 6185
    assert result["expected_check_digit"] == 3
    assert result["actual_check_digit"] == 3
    assert result["passed"] is True


def test_category_identifiers_j_and_z_accepted() -> None:
    for category in "JZ":
        number = f"ABC{category}0000014"  # 末位 4 仅占位，下面独立复算
        prefix = number[:10]
        expected = _reference_weighted_sum(prefix) % 11
        expected = 0 if expected == 10 else expected
        good = f"{prefix}{expected}"
        response = client.post(VALID_ENDPOINT, json={"container_numbers": [good]})
        assert response.status_code == 200, response.text
        result = response.json()["results"][0]
        assert result["parts"]["category_identifier"] == category
        assert result["passed"] is True


# -------------------------------------------------------- 可解析但校验失败


def test_parseable_but_check_digit_mismatch_still_returns_200() -> None:
    # 前 10 位与通过样例相同，仅末位被手抄偏差成 4：结构合法但校验失败。
    response = client.post(VALID_ENDPOINT, json={"container_numbers": ["CSQU3054384"]})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["passed_count"] == 0
    assert body["failed_count"] == 1
    result = body["results"][0]
    assert result["weighted_sum"] == 6185
    assert result["expected_check_digit"] == 3
    assert result["actual_check_digit"] == 4
    assert result["passed"] is False


def test_remainder_ten_expects_zero_wrong_digit_fails() -> None:
    # AAAU000006 加权和余数 10 -> 期望校验位 0。
    good = client.post(VALID_ENDPOINT, json={"container_numbers": ["AAAU0000060"]})
    assert good.status_code == 200
    good_result = good.json()["results"][0]
    assert good_result["weighted_sum"] == 3398
    assert good_result["expected_check_digit"] == 0
    assert good_result["actual_check_digit"] == 0
    assert good_result["passed"] is True

    bad = client.post(
        VALID_ENDPOINT, json={"container_numbers": ["AAAU0000065"]}
    )
    assert bad.status_code == 200
    bad_result = bad.json()["results"][0]
    assert bad_result["expected_check_digit"] == 0
    assert bad_result["actual_check_digit"] == 5
    assert bad_result["passed"] is False


def test_mixed_batch_preserves_original_indices_and_counts() -> None:
    numbers = ["CSQU3054383", "CSQU3054384", "AAAU0000060", "AAAU0000081"]
    response = client.post(VALID_ENDPOINT, json={"container_numbers": numbers})
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 4
    assert body["passed_count"] == 2
    assert body["failed_count"] == 2
    assert [r["index"] for r in body["results"]] == [0, 1, 2, 3]
    assert [r["passed"] for r in body["results"]] == [True, False, True, False]
    # AAAU000008 余数 0，期望校验位 0，末位 1 即失败。
    last = body["results"][3]
    assert last["weighted_sum"] == 4422
    assert last["expected_check_digit"] == 0
    assert last["actual_check_digit"] == 1


# -------------------------------------------------------------- 箱主汇总开关


def _reference_owner_summary(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """测试侧独立归组：先取箱主首次出现顺序，再逐箱主整批重扫计数，
    与服务端单次遍历的分组实现刻意不同。"""
    owners = list(dict.fromkeys(r["parts"]["owner_code"] for r in results))
    return [
        {
            "owner_code": owner,
            "total": sum(1 for r in results if r["parts"]["owner_code"] == owner),
            "passed": sum(
                1
                for r in results
                if r["parts"]["owner_code"] == owner and r["passed"]
            ),
            "failed": sum(
                1
                for r in results
                if r["parts"]["owner_code"] == owner and not r["passed"]
            ),
        }
        for owner in owners
    ]


# 交错箱主批次：同一箱主多次出现且不相邻，校验结论通过/未通过混杂。
INTERLEAVED_NUMBERS = [
    "CSQU3054383",  # CSQ 通过
    "AAAU0000060",  # AAA 通过
    "CSQU3054384",  # CSQ 未通过
    "MSCU6355890",  # MSC（结论由独立复算判定）
    "AAAU0000081",  # AAA 未通过
    "CSQU3054383",  # CSQ 通过（重复箱号）
]


def test_owner_summary_interleaved_order_and_counts() -> None:
    response = client.post(
        VALID_ENDPOINT,
        json={
            "container_numbers": INTERLEAVED_NUMBERS,
            "include_owner_summary": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    # 一次调用同时取得逐箱结论与班组统计。
    assert [r["index"] for r in body["results"]] == list(range(6))
    summary = body["owner_summary"]
    # 首次出现顺序钉死为 CSQ -> AAA -> MSC，重复箱主只形成一项。
    assert [g["owner_code"] for g in summary] == ["CSQ", "AAA", "MSC"]
    assert len(summary) == 3
    # 计数与测试侧独立归组（整批重扫）一致，不受分组实现影响。
    assert summary == _reference_owner_summary(body["results"])
    # 各组之和恒等于整批计数。
    assert sum(g["total"] for g in summary) == body["count"] == 6
    assert sum(g["passed"] for g in summary) == body["passed_count"]
    assert sum(g["failed"] for g in summary) == body["failed_count"]
    for group in summary:
        assert group["passed"] + group["failed"] == group["total"]


def test_owner_summary_single_owner_batch_forms_one_entry() -> None:
    response = client.post(
        VALID_ENDPOINT,
        json={
            "container_numbers": ["CSQU3054383", "CSQU3054384"],
            "include_owner_summary": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["owner_summary"] == [
        {"owner_code": "CSQ", "total": 2, "passed": 1, "failed": 1}
    ]


# 旧请求快照：引入开关前的请求与响应形态，逐字段锁定兼容行为。
LEGACY_SNAPSHOT_REQUEST = {"container_numbers": ["CSQU3054383", "CSQU3054384"]}
LEGACY_SNAPSHOT_RESPONSE = {
    "status": "ok",
    "count": 2,
    "passed_count": 1,
    "failed_count": 1,
    "results": [
        {
            "index": 0,
            "container_number": "CSQU3054383",
            "parts": {
                "owner_code": "CSQ",
                "category_identifier": "U",
                "serial_number": "305438",
                "check_digit": "3",
            },
            "weighted_sum": 6185,
            "expected_check_digit": 3,
            "actual_check_digit": 3,
            "passed": True,
        },
        {
            "index": 1,
            "container_number": "CSQU3054384",
            "parts": {
                "owner_code": "CSQ",
                "category_identifier": "U",
                "serial_number": "305438",
                "check_digit": "4",
            },
            "weighted_sum": 6185,
            "expected_check_digit": 3,
            "actual_check_digit": 4,
            "passed": False,
        },
    ],
}


def test_verify_without_switch_matches_legacy_snapshot() -> None:
    # 省略开关：响应与旧快照逐字段一致，原有客户端无需处理新字段。
    response = client.post(VALID_ENDPOINT, json=LEGACY_SNAPSHOT_REQUEST)
    assert response.status_code == 200
    body = response.json()
    assert body == LEGACY_SNAPSHOT_RESPONSE
    assert "owner_summary" not in body


def test_verify_switch_false_identical_to_omitted() -> None:
    # 开关为假：与省略开关的响应逐字段一致。
    omitted = client.post(VALID_ENDPOINT, json=LEGACY_SNAPSHOT_REQUEST)
    switched_off = client.post(
        VALID_ENDPOINT,
        json={**LEGACY_SNAPSHOT_REQUEST, "include_owner_summary": False},
    )
    assert switched_off.status_code == 200
    assert switched_off.json() == omitted.json() == LEGACY_SNAPSHOT_RESPONSE
    assert "owner_summary" not in switched_off.json()


# ------------------------------------------------------------- 整批结构拒绝


def test_invalid_batch_rejected_with_minimum_index_and_position() -> None:
    response = client.post(
        VALID_ENDPOINT,
        json={"container_numbers": ["CSQU3054383", "csqu3054383"]},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "invalid_batch"
    assert body["count"] == 2
    assert body["index"] == 1  # 最小非法索引
    assert body["container_number"] == "csqu3054383"
    assert body["error_code"] == "not_uppercase_letter"
    assert body["position"] == 1


def test_first_invalid_index_wins_and_later_items_are_not_inspected() -> None:
    response = client.post(
        VALID_ENDPOINT,
        json={"container_numbers": ["!!", "CSQX3054383", "CSQU3054383"]},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "invalid_batch"
    assert body["index"] == 0
    assert body["error_code"] == "invalid_length"
    assert body["position"] == 3  # 长度 2，越界位置为 3


def test_invalid_category_identifier_reports_position_four() -> None:
    response = client.post(
        VALID_ENDPOINT, json={"container_numbers": ["CSQX3054383"]}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "invalid_batch"
    assert body["index"] == 0
    assert body["error_code"] == "invalid_category_identifier"
    assert body["position"] == 4


def test_whitespace_is_not_normalized_batch_rejected() -> None:
    response = client.post(
        VALID_ENDPOINT,
        json={"container_numbers": [" CSQU3054383"]},  # 前导空格 -> 12 位
    )
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "invalid_batch"
    assert body["error_code"] == "invalid_length"
    assert body["position"] == 12


def test_no_partial_results_when_batch_unparseable() -> None:
    response = client.post(
        VALID_ENDPOINT,
        json={"container_numbers": ["CSQU3054383", "CSQU305438A"]},
    )
    assert response.status_code == 422
    assert "results" not in response.json()


def test_invalid_batch_with_switch_still_rejected_without_summary() -> None:
    # 开关开启不改变整批拒绝语义：仍按最小输入索引返回 422 业务错误，
    # 且不产生任何汇总。
    response = client.post(
        VALID_ENDPOINT,
        json={
            "container_numbers": ["CSQU3054383", "csqu3054383"],
            "include_owner_summary": True,
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "invalid_batch"
    assert body["count"] == 2
    assert body["index"] == 1
    assert body["error_code"] == "not_uppercase_letter"
    assert body["position"] == 1
    assert "results" not in body
    assert "owner_summary" not in body


# -------------------------------------------------------------- 请求形状校验


def test_empty_batch_is_request_validation_error_not_business_error() -> None:
    response = client.post(VALID_ENDPOINT, json={"container_numbers": []})
    assert response.status_code == 422
    body = response.json()
    # Pydantic 标准错误形态，区别于业务负载 {"status": "invalid_batch", ...}。
    assert "detail" in body
    assert body.get("status") != "invalid_batch"


def test_batch_size_limits_one_and_one_hundred() -> None:
    one = client.post(
        VALID_ENDPOINT, json={"container_numbers": ["CSQU3054383"]}
    )
    assert one.status_code == 200
    assert one.json()["count"] == 1

    hundred = client.post(
        VALID_ENDPOINT,
        json={"container_numbers": ["CSQU3054383"] * 100},
    )
    assert hundred.status_code == 200
    assert len(hundred.json()["results"]) == 100
    assert hundred.json()["passed_count"] == 100

    too_many = client.post(
        VALID_ENDPOINT,
        json={"container_numbers": ["CSQU3054383"] * 101},
    )
    assert too_many.status_code == 422
    assert "detail" in too_many.json()


def test_missing_field_wrong_type_and_extra_field_rejected() -> None:
    missing: dict[str, Any] = {}
    assert client.post(VALID_ENDPOINT, json=missing).status_code == 422

    wrong_type = {"container_numbers": ["CSQU3054383", 12345678901]}
    wrong = client.post(VALID_ENDPOINT, json=wrong_type)
    assert wrong.status_code == 422
    assert "detail" in wrong.json()

    extra = {
        "container_numbers": ["CSQU3054383"],
        "normalize": True,
    }
    assert client.post(VALID_ENDPOINT, json=extra).status_code == 422


def test_owner_summary_switch_wrong_type_is_request_validation_error() -> None:
    # 开关只接受 JSON true/false；字符串、数字等类型错误的值一律进入
    # Pydantic 的 422 detail，而非业务负载，也不做宽松转换。
    for bad_value in ("yes", "true", 1, 0):
        response = client.post(
            VALID_ENDPOINT,
            json={
                "container_numbers": ["CSQU3054383"],
                "include_owner_summary": bad_value,
            },
        )
        assert response.status_code == 422, bad_value
        body = response.json()
        assert "detail" in body
        assert body.get("status") != "invalid_batch"


def test_echo_does_not_mutate_input() -> None:
    response = client.post(
        VALID_ENDPOINT, json={"container_numbers": ["AAAU0000060"]}
    )
    assert response.json()["results"][0]["container_number"] == "AAAU0000060"


# -------------------------------------------------------------- 单箱纠错建议


def test_correct_unique_candidate_matches_independent_hamming_scan() -> None:
    # 类别码 X 非法：只有修成 U 后校验位吻合，其余单字符改动均不合法。
    response = client.post(CORRECT_ENDPOINT, json={"container_number": "CSQX3054383"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unique"
    assert body["container_number"] == "CSQX3054383"
    assert body["candidate_count"] == 1
    assert body["candidates"] == _reference_corrections("CSQX3054383")
    assert body["candidates"][0] == {
        "position": 4,
        "original_character": "X",
        "replacement_character": "U",
        "container_number": "CSQU3054383",
    }


def test_correct_multiple_candidates_stable_order_and_hamming_one() -> None:
    # 末位抄错的经典样例：歧义情形，候选多于一个。
    response = client.post(CORRECT_ENDPOINT, json={"container_number": "CSQU3054384"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "multiple"
    assert body["candidate_count"] == len(body["candidates"]) > 1
    assert body["candidates"] == _reference_corrections("CSQU3054384")
    # 每个候选与原值汉明距离恰为 1，且不包含原号自身。
    for candidate in body["candidates"]:
        assert _hamming_distance(candidate["container_number"], "CSQU3054384") == 1
        assert candidate["container_number"] != "CSQU3054384"
    # 稳定排序：按（差异位置, 替换字符）升序。
    keys = [(c["position"], c["replacement_character"]) for c in body["candidates"]]
    assert keys == sorted(keys)


def test_correct_no_candidate_still_returns_200() -> None:
    # 合法请求但无任何单字符修复能同时满足结构与校验位。
    response = client.post(CORRECT_ENDPOINT, json={"container_number": "CSQX3054380"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "not_found"
    assert body["candidate_count"] == 0
    assert body["candidates"] == []
    assert _reference_corrections("CSQX3054380") == []


def test_correct_valid_input_never_echoes_itself() -> None:
    # 原号本身合法时，候选是其他合法号，原号（汉明距离 0）不得混入。
    response = client.post(CORRECT_ENDPOINT, json={"container_number": "CSQU3054383"})
    assert response.status_code == 200
    body = response.json()
    assert body["candidates"] == _reference_corrections("CSQU3054383")
    assert body["candidate_count"] == len(body["candidates"])
    for candidate in body["candidates"]:
        assert _hamming_distance(candidate["container_number"], "CSQU3054383") == 1


def test_correct_does_not_normalize_case_or_whitespace() -> None:
    # 小写输入原样处理（不归一化为大写后再纠错），候选由独立参考实现界定。
    response = client.post(CORRECT_ENDPOINT, json={"container_number": "csqu3054383"})
    assert response.status_code == 200
    body = response.json()
    assert body["container_number"] == "csqu3054383"  # 原样回显
    assert body["candidates"] == _reference_corrections("csqu3054383")

    # 前导空白使长度为 12，按请求校验拒绝而非 trim。
    spaced = client.post(CORRECT_ENDPOINT, json={"container_number": " CSQU3054383"})
    assert spaced.status_code == 422
    assert "detail" in spaced.json()


def test_correct_request_shape_errors_are_422() -> None:
    too_short = client.post(CORRECT_ENDPOINT, json={"container_number": "CSQU305438"})
    assert too_short.status_code == 422
    assert "detail" in too_short.json()

    too_long = client.post(
        CORRECT_ENDPOINT, json={"container_number": "CSQU30543834"}
    )
    assert too_long.status_code == 422

    wrong_type = client.post(CORRECT_ENDPOINT, json={"container_number": 12345678901})
    assert wrong_type.status_code == 422

    missing = client.post(CORRECT_ENDPOINT, json={})
    assert missing.status_code == 422

    extra = client.post(
        CORRECT_ENDPOINT,
        json={"container_number": "CSQU3054383", "normalize": True},
    )
    assert extra.status_code == 422


# ---------------------------------------------------- 先校验后纠错（回归旧接口）


def test_verify_then_correct_flow_and_verify_regression() -> None:
    # 调用方先批量校验筛出未通过项，再逐项提交纠错入口。
    verify = client.post(
        VALID_ENDPOINT,
        json={"container_numbers": ["CSQU3054383", "CSQU3054384"]},
    )
    assert verify.status_code == 200
    body = verify.json()
    assert body["status"] == "ok"
    assert [r["passed"] for r in body["results"]] == [True, False]

    failed = [r["container_number"] for r in body["results"] if not r["passed"]]
    assert failed == ["CSQU3054384"]

    correction = client.post(CORRECT_ENDPOINT, json={"container_number": failed[0]})
    assert correction.status_code == 200
    assert correction.json()["status"] == "multiple"
    # 真实箱号 CSQU3054383 必在候选之中。
    assert any(
        c["container_number"] == "CSQU3054383"
        for c in correction.json()["candidates"]
    )


# ---------------------------------------------------------- 单箱计算明细


def test_explain_known_sample_ten_products() -> None:
    # 已知样例 CSQU3054383：十项乘积逐项钉死，并与独立参考实现逐步骤对比。
    response = client.post(EXPLAIN_ENDPOINT, json={"container_number": "CSQU3054383"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["container_number"] == "CSQU3054383"
    assert body["steps"] == _reference_steps("CSQU305438")
    assert len(body["steps"]) == 10
    assert [s["product"] for s in body["steps"]] == [
        13,
        60,
        112,
        256,
        48,
        0,
        320,
        512,
        768,
        4096,
    ]
    assert body["weighted_sum"] == 6185
    assert body["remainder"] == 3
    assert body["expected_check_digit"] == 3
    assert body["actual_check_digit"] == 3
    assert body["passed"] is True


def test_explain_remainder_ten_folds_to_zero() -> None:
    # AAAU000006 加权和 3398，余数 10 -> 期望校验位 0；余数本身原样呈现。
    good = client.post(EXPLAIN_ENDPOINT, json={"container_number": "AAAU0000060"})
    assert good.status_code == 200
    body = good.json()
    assert body["weighted_sum"] == 3398
    assert body["remainder"] == 10
    assert body["expected_check_digit"] == 0
    assert body["actual_check_digit"] == 0
    assert body["passed"] is True

    bad = client.post(EXPLAIN_ENDPOINT, json={"container_number": "AAAU0000065"})
    assert bad.status_code == 200
    bad_body = bad.json()
    assert bad_body["remainder"] == 10
    assert bad_body["expected_check_digit"] == 0
    assert bad_body["actual_check_digit"] == 5
    assert bad_body["passed"] is False


def test_explain_remainder_zero_boundary() -> None:
    response = client.post(EXPLAIN_ENDPOINT, json={"container_number": "AAAU0000080"})
    assert response.status_code == 200
    body = response.json()
    assert body["weighted_sum"] == 4422
    assert body["remainder"] == 0
    assert body["expected_check_digit"] == 0
    assert body["passed"] is True


def test_explain_structure_error_reuses_first_damage_position() -> None:
    # 结构损坏：与批量校验同一来源的首个损坏位置与错误代码，业务负载 422。
    cases = [
        ("csqu3054383", "not_uppercase_letter", 1),     # 小写，绝不归一化
        ("CSQX3054383", "invalid_category_identifier", 4),
        ("CSQU30543A3", "not_digit", 10),
        ("CSQU305438A", "not_digit", 11),
        ("CSQU305438", "invalid_length", 11),           # 仅 10 位
        (" CSQU3054383", "invalid_length", 12),         # 前导空白不去除
        ("CSQU3054383 ", "invalid_length", 12),         # 尾随空白不去除
    ]
    for number, code, position in cases:
        response = client.post(EXPLAIN_ENDPOINT, json={"container_number": number})
        assert response.status_code == 422, number
        body = response.json()
        assert body["status"] == "invalid_container"
        assert body["container_number"] == number  # 原样回显，不做任何纠正
        assert body["error_code"] == code
        assert body["position"] == position
        assert "steps" not in body


def test_explain_request_shape_errors_are_422() -> None:
    missing = client.post(EXPLAIN_ENDPOINT, json={})
    assert missing.status_code == 422
    assert "detail" in missing.json()

    wrong_type = client.post(EXPLAIN_ENDPOINT, json={"container_number": 12345678901})
    assert wrong_type.status_code == 422
    assert "detail" in wrong_type.json()

    extra = client.post(
        EXPLAIN_ENDPOINT,
        json={"container_number": "CSQU3054383", "normalize": True},
    )
    assert extra.status_code == 422
    assert "detail" in extra.json()


def test_explain_totals_and_verdict_always_match_verify() -> None:
    # 调用方从批量结果中任取箱号提交明细：合计与结论必须等于原校验结果。
    numbers = [
        "CSQU3054383",  # 通过
        "CSQU3054384",  # 末位不符
        "AAAU0000060",  # 余数 10 折 0
        "AAAU0000081",  # 余数 0 但末位不符
    ]
    verify = client.post(VALID_ENDPOINT, json={"container_numbers": numbers})
    assert verify.status_code == 200
    for result in verify.json()["results"]:
        explain = client.post(
            EXPLAIN_ENDPOINT, json={"container_number": result["container_number"]}
        )
        assert explain.status_code == 200
        body = explain.json()
        # 明细合计由十项乘积求和，且与批量结果同名字段一致。
        steps_total = sum(s["product"] for s in body["steps"])
        assert steps_total == body["weighted_sum"] == result["weighted_sum"]
        assert body["remainder"] == result["weighted_sum"] % 11
        assert body["expected_check_digit"] == result["expected_check_digit"]
        assert body["actual_check_digit"] == result["actual_check_digit"]
        assert body["passed"] == result["passed"]


def test_verify_and_correct_regression_after_explain_added() -> None:
    # 旧接口回归：批量校验与单箱纠错的路径、请求及响应保持兼容。
    verify = client.post(
        VALID_ENDPOINT,
        json={"container_numbers": ["CSQU3054383", "csqu3054383"]},
    )
    assert verify.status_code == 422
    assert verify.json()["status"] == "invalid_batch"
    assert verify.json()["index"] == 1

    correct = client.post(CORRECT_ENDPOINT, json={"container_number": "CSQX3054383"})
    assert correct.status_code == 200
    assert correct.json()["status"] == "unique"
    assert correct.json()["candidates"][0]["container_number"] == "CSQU3054383"


# ------------------------------------------------------- 未配对代理字符

# 请求体 JSON 的 \uXXXX 转义可构造未配对代理字符（如首位为 \ud800 的
# 11 位箱号）。它不属于 A-Z：批量校验与明细入口必须在位置 1 报
# not_uppercase_letter；纠错入口照常生成候选。响应中的原样回显经
# \uXXXX 转义传输，客户端用标准 JSON 解析即可无损还原。
SURROGATE_FIRST_NUMBER = "\ud800SQU3054383"


def _post_raw_json(url: str, payload: Any) -> Any:
    """以原始 JSON 体 POST，可携带 \\uXXXX 转义的未配对代理字符。

    httpx 的 ``json=`` 参数按 ``ensure_ascii=False`` 序列化，无法编码
    未配对代理字符；标准库 ``json.dumps`` 默认转义非 ASCII 字符，
    传输与解析两端均可无损还原。
    """
    return client.post(
        url,
        content=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


def test_verify_rejects_unpaired_surrogate_with_first_position_error() -> None:
    # 首位含未配对代理字符的 11 位箱号：整批拒绝，报首位结构错误。
    response = _post_raw_json(
        VALID_ENDPOINT,
        {"container_numbers": ["CSQU3054383", SURROGATE_FIRST_NUMBER]},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "invalid_batch"
    assert body["count"] == 2
    assert body["index"] == 1  # 最小非法索引
    assert body["container_number"] == SURROGATE_FIRST_NUMBER  # 原样回显
    assert body["error_code"] == "not_uppercase_letter"
    assert body["position"] == 1
    assert "results" not in body


def test_explain_unpaired_surrogate_returns_first_position_error() -> None:
    # 明细入口：同样返回首位结构错误，而不是中断响应。
    response = _post_raw_json(
        EXPLAIN_ENDPOINT, {"container_number": SURROGATE_FIRST_NUMBER}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "invalid_container"
    assert body["container_number"] == SURROGATE_FIRST_NUMBER
    assert body["error_code"] == "not_uppercase_letter"
    assert body["position"] == 1
    assert "steps" not in body


def test_correct_unpaired_surrogate_returns_full_candidates() -> None:
    # 纠错入口：合法候选完整返回，结论不被代理字符中断。
    response = _post_raw_json(
        CORRECT_ENDPOINT, {"container_number": SURROGATE_FIRST_NUMBER}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "multiple"
    assert body["container_number"] == SURROGATE_FIRST_NUMBER
    # 候选与测试侧独立汉明距离枚举完全一致（位置 1 候选的原字符即代理字符）。
    assert body["candidates"] == _reference_corrections(SURROGATE_FIRST_NUMBER)
    assert body["candidate_count"] == len(body["candidates"]) > 0
    position_one = [c for c in body["candidates"] if c["position"] == 1]
    assert position_one
    assert all(c["original_character"] == "\ud800" for c in position_one)


def test_unpaired_surrogate_variants_and_astral_character() -> None:
    # 低代理字符同样按位置 1 结构错误拒绝。
    low = _post_raw_json(EXPLAIN_ENDPOINT, {"container_number": "\udfffSQU3054383"})
    assert low.status_code == 422
    assert low.json()["error_code"] == "not_uppercase_letter"
    assert low.json()["position"] == 1
    assert low.json()["container_number"] == "\udfffSQU3054383"

    # 合法码点（emoji，UTF-16 中为代理对）本就无需特殊处理，行为不变。
    emoji = _post_raw_json(
        EXPLAIN_ENDPOINT, {"container_number": "\U0001f600SQU3054383"}
    )
    assert emoji.status_code == 422
    assert emoji.json()["error_code"] == "not_uppercase_letter"
    assert emoji.json()["position"] == 1
    assert emoji.json()["container_number"] == "\U0001f600SQU3054383"


def test_request_shape_error_with_surrogate_input_still_returns_detail() -> None:
    # 12 位（超长）触发请求形状校验；detail 会原样回显含代理字符的
    # 输入，必须仍是 422 detail 形态，而不是在渲染错误时 500。
    response = _post_raw_json(
        CORRECT_ENDPOINT, {"container_number": SURROGATE_FIRST_NUMBER + "X"}
    )
    assert response.status_code == 422
    assert "detail" in response.json()


# ------------------------------------------------------------ 一次性清单核对

# 经测试侧独立参考实现确认合法的箱号（校验位均通过）。
R_A = "CSQU3054383"  # 加权和 6185，余 3，校验位 3
R_B = "AAAU0000060"  # 加权和 3398，余 10 折 0
R_C = "BBBU0000000"  # 加权和 340，余 10 折 0
R_D = "ABCU1234560"  # 加权和 5478，余 0
R_E = "MSCU6355895"  # 加权和 8200，余 5
# 结构合法但校验位不符。
R_A_BAD = "CSQU3054384"


def _reconcile(expected: object, onsite: object) -> Any:
    """以两份清单调用一次性核对入口（允许传入任意形状用于 422 断言）。"""
    return client.post(
        RECONCILE_ENDPOINT,
        json={
            "expected_container_numbers": expected,
            "onsite_container_numbers": onsite,
        },
    )


def _reference_reconcile(
    expected: list[str], onsite: list[str]
) -> dict[str, list[Any]]:
    """测试侧独立配对参考：逐箱号收集两侧索引后按出现次序对齐。

    与服务端的 FIFO 队列实现刻意不同；matched/missing 按预期索引排序，
    extra 按现场索引排序，以对应契约规定的清单顺序。
    """
    epos: dict[str, list[int]] = {}
    opos: dict[str, list[int]] = {}
    for i, n in enumerate(expected):
        epos.setdefault(n, []).append(i)
    for i, n in enumerate(onsite):
        opos.setdefault(n, []).append(i)

    matched: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    extra: list[dict[str, Any]] = []
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
    extra.sort(key=lambda m: m["onsite_index"])
    return {"matched": matched, "missing": missing, "extra": extra}


def _assert_reconcile_counts(body: dict[str, Any]) -> None:
    """配对数量守恒：各项数与列表长度、两侧清单长度一致。"""
    assert body["matched_count"] == len(body["matched"])
    assert body["missing_count"] == len(body["missing"])
    assert body["extra_count"] == len(body["extra"])
    assert body["matched_count"] + body["missing_count"] == body["expected_count"]
    assert body["matched_count"] + body["extra_count"] == body["onsite_count"]


def test_reconcile_identical_lists_all_matched() -> None:
    # 场景一：完全一致（乱序）仍全部配对，原索引各自正确。
    expected = [R_A, R_B, R_C]
    onsite = [R_C, R_A, R_B]
    response = _reconcile(expected, onsite)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["expected_count"] == 3
    assert body["onsite_count"] == 3
    assert (body["matched_count"], body["missing_count"], body["extra_count"]) == (
        3,
        0,
        0,
    )
    # matched 按预期清单顺序，onsite_index 指向乱序后的现场位置。
    assert body["matched"] == [
        {"container_number": R_A, "expected_index": 0, "onsite_index": 1},
        {"container_number": R_B, "expected_index": 1, "onsite_index": 2},
        {"container_number": R_C, "expected_index": 2, "onsite_index": 0},
    ]
    assert body["missing"] == []
    assert body["extra"] == []
    _assert_reconcile_counts(body)


def test_reconcile_single_sided_missing_and_extra() -> None:
    # 场景二：单侧缺失与现场多出并存（乱序）。
    expected = [R_A, R_B, R_C]
    onsite = [R_D, R_A, R_B]  # 缺 R_C，多 R_D
    response = _reconcile(expected, onsite)
    assert response.status_code == 200
    body = response.json()
    assert (body["matched_count"], body["missing_count"], body["extra_count"]) == (
        2,
        1,
        1,
    )
    assert body["matched"] == [
        {"container_number": R_A, "expected_index": 0, "onsite_index": 1},
        {"container_number": R_B, "expected_index": 1, "onsite_index": 2},
    ]
    # 缺少项按预期清单顺序，携带预期原索引。
    assert body["missing"] == [
        {"container_number": R_C, "expected_index": 2}
    ]
    # 多出项按现场清单顺序，携带现场原索引。
    assert body["extra"] == [{"container_number": R_D, "onsite_index": 0}]
    _assert_reconcile_counts(body)


def test_reconcile_duplicate_counts_unequal_only_last_occurrence_differs() -> None:
    # 场景三：重复次数不等。同一箱号一侧三次、另一侧两次时，只有最后一次
    # （按各自清单顺序）归入差异。
    # 预期 3 次 R_A、现场 2 次：预期最后一次（索引 3）为 missing。
    response = _reconcile(
        [R_A, R_B, R_A, R_A],
        [R_A, R_A, R_B],
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["matched_count"], body["missing_count"], body["extra_count"]) == (
        3,
        1,
        0,
    )
    assert body["missing"] == [{"container_number": R_A, "expected_index": 3}]
    assert body["extra"] == []
    _assert_reconcile_counts(body)

    # 现场 3 次、预期 2 次：现场最后一次（索引 2）为 extra。
    response = _reconcile([R_B, R_A], [R_A, R_B, R_A])
    assert response.status_code == 200
    body = response.json()
    assert (body["matched_count"], body["missing_count"], body["extra_count"]) == (
        2,
        0,
        1,
    )
    assert body["missing"] == []
    assert body["extra"] == [{"container_number": R_A, "onsite_index": 2}]
    _assert_reconcile_counts(body)


def test_reconcile_shuffled_duplicated_samples_match_reference_and_conserve() -> None:
    # 乱序及重复样例：与测试侧独立参考逐字段一致，且配对数量守恒。
    samples = [
        ([R_A, R_B, R_A, R_C, R_B], [R_B, R_A, R_C, R_A, R_B]),
        ([R_A, R_A, R_B], [R_B, R_A, R_A]),
        ([R_C, R_A, R_D, R_B, R_E], [R_E, R_B, R_D, R_A, R_D]),
        ([R_A, R_B, R_C, R_A], [R_A, R_A, R_B, R_C]),
        ([R_E, R_E, R_D, R_E, R_D], [R_D, R_E, R_D, R_E, R_E]),
        ([R_A, R_B, R_C, R_D, R_E], [R_E, R_D, R_C, R_B, R_A]),
    ]
    for expected, onsite in samples:
        response = _reconcile(expected, onsite)
        assert response.status_code == 200, (expected, onsite, response.text)
        body = response.json()
        reference = _reference_reconcile(expected, onsite)
        assert body["matched"] == reference["matched"], (expected, onsite)
        assert body["missing"] == reference["missing"], (expected, onsite)
        assert body["extra"] == reference["extra"], (expected, onsite)
        _assert_reconcile_counts(body)
        # 原索引序列不重不漏：预期索引恰好覆盖 0..n-1，现场索引同理。
        assert sorted(
            [m["expected_index"] for m in body["matched"]]
            + [m["expected_index"] for m in body["missing"]]
        ) == list(range(len(expected)))
        assert sorted(
            [m["onsite_index"] for m in body["matched"]]
            + [x["onsite_index"] for x in body["extra"]]
        ) == list(range(len(onsite)))


def test_reconcile_invalid_structure_in_expected_list_rejected() -> None:
    # 场景四（结构非法）：预期清单含小写箱号，整次核对拒绝，明确来源、
    # 最小输入索引与原校验结论（结构错误）。
    response = _reconcile([R_A, "csqu3054383"], [R_A])
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "invalid_item"
    assert body["expected_count"] == 2
    assert body["onsite_count"] == 1
    assert body["list_source"] == "expected"
    assert body["index"] == 1
    assert body["container_number"] == "csqu3054383"
    assert body["error_code"] == "not_uppercase_letter"
    assert body["position"] == 1
    assert body["expected_check_digit"] is None
    assert body["actual_check_digit"] is None
    assert body["passed"] is False
    # 拒绝负载不携带任何配对结果。
    assert "matched" not in body
    assert "missing" not in body
    assert "extra" not in body


def test_reconcile_invalid_structure_in_onsite_list_rejected() -> None:
    # 现场清单的结构非法项（预期清单已全部有效）：来源 onsite。
    response = _reconcile([R_A], [R_A, "CSQX3054383"])
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "invalid_item"
    assert body["list_source"] == "onsite"
    assert body["index"] == 1
    assert body["container_number"] == "CSQX3054383"
    assert body["error_code"] == "invalid_category_identifier"
    assert body["position"] == 4
    assert body["expected_check_digit"] is None
    assert body["actual_check_digit"] is None
    assert body["passed"] is False


def test_reconcile_check_digit_failure_rejected_with_original_verdict() -> None:
    # 场景四（校验失败）：结构合法但末位不符，整次核对拒绝，结构字段为
    # null，原校验结论以期望/实际校验位给出。
    response = _reconcile([R_A], [R_A_BAD])
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "invalid_item"
    assert body["list_source"] == "onsite"
    assert body["index"] == 0
    assert body["container_number"] == R_A_BAD
    assert body["error_code"] is None
    assert body["position"] is None
    assert body["message"] is None
    assert body["expected_check_digit"] == 3
    assert body["actual_check_digit"] == 4
    assert body["passed"] is False


def test_reconcile_expected_list_checked_before_onsite_list() -> None:
    # 两侧都含无效项时先报预期清单（即使现场侧无效索引更小）。
    response = _reconcile([R_A, "CSQX3054383"], ["!!", R_A])
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "invalid_item"
    assert body["list_source"] == "expected"
    assert body["index"] == 1


def test_reconcile_reuses_structure_source_minimum_index_wins() -> None:
    # 与批量校验同一结构来源：最小无效索引、首个损坏位置、错误码一致。
    response = _reconcile(["!!", "csqu3054383", R_A], [R_A])
    assert response.status_code == 422
    body = response.json()
    assert body["index"] == 0
    assert body["error_code"] == "invalid_length"
    assert body["position"] == 3
    assert body["list_source"] == "expected"


def test_reconcile_request_shape_errors_return_standard_detail() -> None:
    # 空清单、超过 100 项、字段类型错误、缺字段、多余字段：一律标准
    # 422 detail，而不是业务负载 invalid_item。
    empty = _reconcile([], [R_A])
    assert empty.status_code == 422
    assert "detail" in empty.json()
    assert empty.json().get("status") != "invalid_item"

    empty_onsite = _reconcile([R_A], [])
    assert empty_onsite.status_code == 422
    assert "detail" in empty_onsite.json()

    too_many = _reconcile([R_A] * 101, [R_A])
    assert too_many.status_code == 422
    assert "detail" in too_many.json()

    too_many_onsite = _reconcile([R_A], [R_A] * 101)
    assert too_many_onsite.status_code == 422
    assert "detail" in too_many_onsite.json()

    wrong_type = _reconcile([R_A, 12345678901], [R_A])
    assert wrong_type.status_code == 422
    assert "detail" in wrong_type.json()

    missing = client.post(
        RECONCILE_ENDPOINT, json={"expected_container_numbers": [R_A]}
    )
    assert missing.status_code == 422
    assert "detail" in missing.json()

    extra = client.post(
        RECONCILE_ENDPOINT,
        json={
            "expected_container_numbers": [R_A],
            "onsite_container_numbers": [R_A],
            "normalize": True,
        },
    )
    assert extra.status_code == 422
    assert "detail" in extra.json()


def test_reconcile_boundary_sizes_one_and_one_hundred() -> None:
    # 与批量校验一致的批量上限：每侧 1 与 100 项均接受。
    one = _reconcile([R_A], [R_A])
    assert one.status_code == 200
    assert one.json()["matched_count"] == 1

    hundred = _reconcile([R_A] * 100, [R_A] * 100)
    assert hundred.status_code == 200
    body = hundred.json()
    assert body["matched_count"] == 100
    assert body["missing_count"] == 0
    assert body["extra_count"] == 0


def test_reconcile_does_not_disturb_existing_endpoints() -> None:
    # 引入核对入口后，原批量校验、纠错、明细路径与响应保持不变。
    verify = client.post(VALID_ENDPOINT, json={"container_numbers": [R_A, R_A_BAD]})
    assert verify.status_code == 200
    assert [r["passed"] for r in verify.json()["results"]] == [True, False]

    correct = client.post(
        CORRECT_ENDPOINT, json={"container_number": "CSQX3054383"}
    )
    assert correct.status_code == 200
    assert correct.json()["status"] == "unique"

    explain = client.post(EXPLAIN_ENDPOINT, json={"container_number": R_A})
    assert explain.status_code == 200
    assert explain.json()["passed"] is True


def test_reconcile_does_not_normalize_case_or_whitespace() -> None:
    # 小写、前导空白一律不做归一化：结构非法即按业务负载拒绝。
    lower = _reconcile(["csqu3054383"], [R_A])
    assert lower.status_code == 422
    assert lower.json()["error_code"] == "not_uppercase_letter"
    assert lower.json()["position"] == 1

    spaced = _reconcile([R_A], [" CSQU3054383"])  # 12 位
    assert spaced.status_code == 422
    body = spaced.json()
    assert body["status"] == "invalid_item"
    assert body["list_source"] == "onsite"
    assert body["error_code"] == "invalid_length"
    assert body["position"] == 12
