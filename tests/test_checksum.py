"""字符映射与校验位计算的独立测试。

重点覆盖需求点名的两类边界：
* 字母映射跳过所有 11 的倍数（B=12、K=21、L=23、Z=38）；
* 加权和对 11 取余的边界：余数 10 折叠为校验位 0，余数 0 仍为 0，
  其余余数原样成为校验位。
"""

from __future__ import annotations

import dataclasses
import string

import pytest

from app.checksum import (
    CONTAINER_LENGTH,
    LETTER_VALUES,
    character_value,
    checksum_steps,
    correction_candidates,
    expected_check_digit,
    explain_check_digit,
    letter_value,
    split_container_number,
    structure_error,
    weighted_sum,
)

# 以独立的参考实现计算期望值，避免用同一套实现自我作证。
def _reference_letter_values() -> dict[str, int]:
    table: dict[str, int] = {}
    nxt = 10
    for letter in string.ascii_uppercase:
        table[letter] = nxt
        nxt += 1
        if nxt % 11 == 0:
            nxt += 1
    return table


# ---------------------------------------------------------------- 字母映射


def test_all_26_letters_present_in_order() -> None:
    assert list(LETTER_VALUES) == list(string.ascii_uppercase)
    assert set(LETTER_VALUES) == set(string.ascii_uppercase)


@pytest.mark.parametrize(
    ("letter", "expected"),
    [
        ("A", 10),
        ("B", 12),   # 11 是 11 的倍数，跳过
        ("K", 21),
        ("L", 23),   # 22 是 11 的倍数，跳过
        ("V", 34),   # 33 是 11 的倍数，跳过
        ("Z", 38),
    ],
)
def test_letter_values_skip_multiples_of_eleven(letter: str, expected: int) -> None:
    assert letter_value(letter) == expected


def test_no_letter_value_is_a_multiple_of_eleven() -> None:
    assert all(value % 11 != 0 for value in LETTER_VALUES.values())


def test_letter_table_matches_independent_reference() -> None:
    assert LETTER_VALUES == _reference_letter_values()


def test_letter_value_rejects_non_uppercase_inputs() -> None:
    for bad in ("a", "b", "z", "0", " ", "Ａ", ""):
        with pytest.raises(KeyError):
            letter_value(bad)


@pytest.mark.parametrize("digit", list(string.digits))
def test_character_value_digits_keep_face_value(digit: str) -> None:
    assert character_value(digit) == int(digit)


def test_character_value_letters_use_table() -> None:
    assert character_value("A") == 10
    assert character_value("Z") == 38


# ---------------------------------------------------------------- 加权计算


def test_weighted_sum_known_iso6346_sample() -> None:
    # ISO 6346 经典公开样例 CSQU3054383，前 10 位加权和为 6185，余 3。
    assert weighted_sum("CSQU305438") == 6185
    assert 6185 % 11 == 3
    assert expected_check_digit("CSQU305438") == 3


def test_weighted_sum_matches_position_powers_of_two() -> None:
    # 全部由 A 构成时：每位的值都是 10，和为 10 * (2**0+...+2**9)。
    assert weighted_sum("AAAAAAAAAA") == 10 * (2**10 - 1)


def test_weighted_sum_is_position_sensitive() -> None:
    # 同一字符放在不同权重位上，和必须不同，且差异恰为 2 的幂次差。
    assert weighted_sum("BAAAAAAAAA") - weighted_sum("AAAAAAAAAA") == (12 - 10) * 1
    assert weighted_sum("ABAAAAAAAA") - weighted_sum("AAAAAAAAAA") == (12 - 10) * 2
    assert weighted_sum("AAAAAAAAAB") - weighted_sum("AAAAAAAAAA") == (12 - 10) * 2**9


# ---------------------------------------------------------------- 余数边界


def test_remainder_ten_collapses_to_check_digit_zero() -> None:
    prefix = "AAAU000006"
    assert weighted_sum(prefix) == 3398
    assert weighted_sum(prefix) % 11 == 10
    assert expected_check_digit(prefix) == 0


def test_remainder_zero_stays_zero() -> None:
    prefix = "AAAU000008"
    assert weighted_sum(prefix) == 4422
    assert weighted_sum(prefix) % 11 == 0
    assert expected_check_digit(prefix) == 0


@pytest.mark.parametrize(
    ("prefix", "total", "remainder", "check_digit"),
    [
        ("AAAU000008", 4422, 0, 0),   # 余数 0：期望校验位即 0
        ("AAAU000014", 2630, 1, 1),
        ("AAAU000001", 838, 2, 2),
        ("AAAU000003", 1862, 3, 3),
        ("AAAU000005", 2886, 4, 4),
        ("AAAU000007", 3910, 5, 5),
        ("AAAU000009", 4934, 6, 6),
        ("AAAU000000", 326, 7, 7),
        ("AAAU000002", 1350, 8, 8),
        ("AAAU000004", 2374, 9, 9),
        ("AAAU000006", 3398, 10, 0),  # 余数 10：折叠为校验位 0
    ],
)
def test_every_possible_remainder_maps_to_expected_check_digit(
    prefix: str, total: int, remainder: int, check_digit: int
) -> None:
    assert weighted_sum(prefix) == total
    assert total % 11 == remainder
    assert expected_check_digit(prefix) == check_digit


def test_expected_check_digit_is_single_decimal_digit() -> None:
    # 任意合法前 10 位算出的期望校验位都必须落在 0..9（余数 10 已折叠）。
    for serial in range(300):
        candidate = "AAAU" + f"{serial:06d}"
        assert 0 <= expected_check_digit(candidate) <= 9


# ---------------------------------------------------------------- 结构判定


def test_structure_error_accepts_canonical_numbers() -> None:
    for number in ("CSQU3054383", "AAAU0000060", "MSCU6355890", "ABJZ1234560"):
        assert structure_error(number) is None
        assert len(number) == CONTAINER_LENGTH


@pytest.mark.parametrize(
    ("number", "code", "position"),
    [
        ("CSQU305438", "invalid_length", 11),              # 仅 10 位
        ("CSQU30543834", "invalid_length", 12),            # 12 位
        ("csqu3054383", "not_uppercase_letter", 1),        # 小写，绝不归一化
        ("CSQu3054383", "invalid_category_identifier", 4), # 第 4 位小写 u
        ("1SQU3054383", "not_uppercase_letter", 1),        # 数字混入箱主码
        ("CSQX3054383", "invalid_category_identifier", 4), # 类别码 X
        ("CSQD3054383", "invalid_category_identifier", 4), # 类别码 D
        ("CSQV3054383", "invalid_category_identifier", 4), # 类别码 V
        ("CSQU305438A", "not_digit", 11),                  # 末位非数字
        ("CSQU30543A3", "not_digit", 10),                  # 顺序号含字母
        (" CSQU3054383", "invalid_length", 12),            # 前导空白不去除
        ("CSQU3054383 ", "invalid_length", 12),            # 尾随空白不去除
        ("CSQＵ3054383", "invalid_category_identifier", 4), # 全角字符
        ("", "invalid_length", 1),
    ],
)
def test_structure_error_locates_first_damaged_position(
    number: str, code: str, position: int
) -> None:
    error = structure_error(number)
    assert error is not None
    assert error.code == code
    assert error.position == position


def test_structure_check_reports_leftmost_error_first() -> None:
    # 同时有多处损坏时，必须报从左到右第一处。
    error = structure_error("12QX5A789X1")
    assert error is not None
    assert (error.code, error.position) == ("not_uppercase_letter", 1)


def test_split_container_number_fields() -> None:
    parts = split_container_number("CSQU3054383")
    assert parts.owner_code == "CSQ"
    assert parts.category_identifier == "U"
    assert parts.serial_number == "305438"
    assert parts.check_digit == "3"


# ---------------------------------------------------------------- 纠错候选


def _hamming_distance(a: str, b: str) -> int:
    assert len(a) == len(b)
    return sum(x != y for x, y in zip(a, b))


def test_correction_candidates_unique_for_invalid_category() -> None:
    candidates = correction_candidates("CSQX3054383")
    assert len(candidates) == 1
    only = candidates[0]
    assert only.position == 4
    assert only.original_character == "X"
    assert only.replacement_character == "U"
    assert only.container_number == "CSQU3054383"


def test_correction_candidates_empty_when_no_single_fix_works() -> None:
    assert correction_candidates("CSQX3054380") == []


def test_correction_candidates_are_valid_and_exactly_one_away() -> None:
    original = "CSQU3054384"
    candidates = correction_candidates(original)
    assert len(candidates) > 1  # 歧义情形
    for candidate in candidates:
        # 候选自身结构合法、校验位通过（复用本模块判定复核）。
        assert structure_error(candidate.container_number) is None
        assert expected_check_digit(candidate.container_number[:10]) == int(
            candidate.container_number[10]
        )
        # 与原值汉明距离恰为 1，且差异位置/字符与记录一致。
        assert _hamming_distance(candidate.container_number, original) == 1
        assert candidate.container_number[: candidate.position - 1] == original[
            : candidate.position - 1
        ]
        assert candidate.container_number[candidate.position :] == original[
            candidate.position :
        ]
        assert candidate.container_number[candidate.position - 1] == (
            candidate.replacement_character
        )
        assert original[candidate.position - 1] == candidate.original_character
        assert candidate.replacement_character != candidate.original_character


def test_correction_candidates_sorted_by_position_then_replacement() -> None:
    candidates = correction_candidates("CSQU3054384")
    keys = [(c.position, c.replacement_character) for c in candidates]
    assert keys == sorted(keys)


def test_correction_candidates_never_include_original() -> None:
    # 合法输入的候选是其他合法号；原号自身（汉明距离 0）不得混入。
    candidates = correction_candidates("CSQU3054383")
    assert candidates
    assert all(c.container_number != "CSQU3054383" for c in candidates)
    assert all(
        _hamming_distance(c.container_number, "CSQU3054383") == 1
        for c in candidates
    )


def test_correction_candidates_require_exact_length() -> None:
    for bad in ("CSQU305438", "CSQU30543834", "", " CSQU3054383"):
        with pytest.raises(ValueError):
            correction_candidates(bad)


# ---------------------------------------------------------------- 计算明细


def test_checksum_steps_known_sample_ten_products() -> None:
    # ISO 6346 经典样例 CSQU305438 的十项乘积逐项钉死。
    steps = checksum_steps("CSQU305438")
    assert len(steps) == 10
    assert [s.position for s in steps] == list(range(1, 11))
    assert [s.character for s in steps] == list("CSQU305438")
    assert [s.value for s in steps] == [13, 30, 28, 32, 3, 0, 5, 4, 3, 8]
    assert [s.weight for s in steps] == [2**i for i in range(10)]
    assert [s.product for s in steps] == [
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


def test_checksum_steps_weight_is_power_of_two_by_position() -> None:
    steps = checksum_steps("AAAU000006")
    for step in steps:
        assert step.weight == 2 ** (step.position - 1)
        assert step.product == step.value * step.weight


def test_checksum_steps_are_immutable() -> None:
    step = checksum_steps("CSQU305438")[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        step.product = 0  # type: ignore[misc]
    explanation = explain_check_digit("CSQU3054383")
    with pytest.raises(dataclasses.FrozenInstanceError):
        explanation.weighted_sum = 0  # type: ignore[misc]


def test_weighted_sum_is_aggregated_from_steps() -> None:
    # 加权和必须由步骤乘积求和得到，二者不得各算一套。
    for prefix in ("CSQU305438", "AAAU000006", "AAAU000008", "MSCU635589"):
        steps = checksum_steps(prefix)
        assert weighted_sum(prefix) == sum(s.product for s in steps)


def test_explain_aggregates_everything_from_steps() -> None:
    explanation = explain_check_digit("CSQU3054383")
    assert explanation.container_number == "CSQU3054383"
    assert explanation.steps == checksum_steps("CSQU305438")
    assert explanation.weighted_sum == sum(s.product for s in explanation.steps)
    assert explanation.weighted_sum == weighted_sum("CSQU305438") == 6185
    assert explanation.remainder == 6185 % 11 == 3
    assert explanation.expected_check_digit == expected_check_digit("CSQU305438")
    assert explanation.actual_check_digit == 3
    assert explanation.passed is True


def test_explain_remainder_ten_folds_to_zero() -> None:
    explanation = explain_check_digit("AAAU0000060")
    assert explanation.weighted_sum == 3398
    assert explanation.remainder == 10  # 原始余数保留 10，不在明细层折叠
    assert explanation.expected_check_digit == 0
    assert explanation.actual_check_digit == 0
    assert explanation.passed is True


def test_explain_remainder_zero_stays_zero() -> None:
    explanation = explain_check_digit("AAAU0000080")
    assert explanation.weighted_sum == 4422
    assert explanation.remainder == 0
    assert explanation.expected_check_digit == 0
    assert explanation.passed is True


def test_explain_always_agrees_with_original_check_functions() -> None:
    # 明细合计与结论必须始终等于原校验函数的结果（含被篡改的末位）。
    for serial in range(300):
        prefix = "AAAU" + f"{serial:06d}"
        expected = expected_check_digit(prefix)
        for check in {expected, (expected + 1) % 10}:
            number = f"{prefix}{check}"
            explanation = explain_check_digit(number)
            assert explanation.weighted_sum == weighted_sum(prefix)
            assert explanation.remainder == weighted_sum(prefix) % 11
            assert explanation.expected_check_digit == expected
            assert explanation.actual_check_digit == check
            assert explanation.passed is (expected == check)


def test_explain_requires_exact_length() -> None:
    for bad in ("CSQU305438", "CSQU30543834", "", " CSQU3054383"):
        with pytest.raises(ValueError):
            explain_check_digit(bad)
