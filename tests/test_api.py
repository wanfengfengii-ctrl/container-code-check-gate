"""批量校验 API 的端到端测试（经 FastAPI TestClient，不打网络栈）。

关键区分：
* 请求本身不符 schema（空批、超过 100 项、类型错误、多余字段）->
  FastAPI/Pydantic 标准 422（detail 数组）；
* 请求形状正确但某个箱号结构非法 -> 业务负载 status=invalid_batch，
  整批 422 拒绝并给出最小输入索引与首个损坏位置；
* 结构全部合法 -> 200，逐项可复算结论，校验位不符的箱仅 passed=false。
"""

from __future__ import annotations

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


def test_echo_does_not_mutate_input() -> None:
    response = client.post(
        VALID_ENDPOINT, json={"container_numbers": ["AAAU0000060"]}
    )
    assert response.json()["results"][0]["container_number"] == "AAAU0000060"
