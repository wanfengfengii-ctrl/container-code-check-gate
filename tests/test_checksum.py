"""字符映射与校验位计算的独立测试。

重点覆盖需求点名的两类边界：
* 字母映射跳过所有 11 的倍数（B=12、K=21、L=23、Z=38）；
* 加权和对 11 取余的边界：余数 10 折叠为校验位 0，余数 0 仍为 0，
  其余余数原样成为校验位。
"""

from __future__ import annotations

import dataclasses
import itertools
import random
import string

import pytest

from app.checksum import (
    CONSENSUS_SOLUTION_LIMIT,
    CONTAINER_LENGTH,
    EXPECTED_LIST,
    LETTER_VALUES,
    ONSITE_LIST,
    ConsensusResult,
    OwnerSummary,
    ReconcileInvalidItem,
    ReconcileResult,
    character_value,
    checksum_steps,
    consensus_container_number,
    correction_candidates,
    expected_check_digit,
    explain_check_digit,
    letter_value,
    reconcile_container_numbers,
    split_container_number,
    structure_error,
    summarize_by_owner,
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


# ---------------------------------------------------------------- 箱主汇总


def test_summarize_by_owner_interleaved_first_seen_order() -> None:
    # 交错输入：返回顺序按箱主首次出现，而非字母序或分组实现细节。
    verdicts = [
        ("CSQ", True),
        ("AAA", False),
        ("CSQ", False),
        ("MSC", True),
        ("AAA", True),
        ("CSQ", True),
    ]
    summary = summarize_by_owner(verdicts)
    assert [group.owner_code for group in summary] == ["CSQ", "AAA", "MSC"]
    assert [(g.total, g.passed, g.failed) for g in summary] == [
        (3, 2, 1),
        (2, 1, 1),
        (1, 1, 0),
    ]


def test_summarize_by_owner_duplicate_owner_forms_single_entry() -> None:
    summary = summarize_by_owner([("CSQ", True), ("CSQ", False), ("CSQ", True)])
    assert summary == (
        OwnerSummary(owner_code="CSQ", total=3, passed=2, failed=1),
    )


def test_summarize_by_owner_counts_match_independent_rescan() -> None:
    # 测试侧用逐箱主整批重扫的独立归组，复核单次遍历的计数结果。
    verdicts = [("AAA", True), ("CSQ", False), ("AAA", False), ("CSQ", True)]
    summary = summarize_by_owner(verdicts)
    owners = list(dict.fromkeys(owner for owner, _ in verdicts))
    expected = [
        (
            owner,
            sum(1 for o, _ in verdicts if o == owner),
            sum(1 for o, p in verdicts if o == owner and p),
            sum(1 for o, p in verdicts if o == owner and not p),
        )
        for owner in owners
    ]
    assert [(g.owner_code, g.total, g.passed, g.failed) for g in summary] == expected
    # 每组恒有 passed + failed == total，各组之和恒等于输入条数。
    for group in summary:
        assert group.passed + group.failed == group.total
    assert sum(g.total for g in summary) == len(verdicts)


def test_summarize_by_owner_empty_input() -> None:
    assert summarize_by_owner([]) == ()


def test_owner_summary_is_immutable() -> None:
    group = summarize_by_owner([("CSQ", True)])[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        group.total = 0  # type: ignore[misc]


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


# ------------------------------------------------------- 未配对代理字符


def test_structure_error_unpaired_surrogate_is_first_position_error() -> None:
    # 请求体 JSON 的 \uXXXX 转义可构造未配对代理字符；它不属于 A-Z，
    # 结构判定必须在位置 1 报 not_uppercase_letter（不做任何归一化）。
    for surrogate in ("\ud800", "\ud83d", "\udfff"):
        number = surrogate + "SQU3054383"
        assert len(number) == CONTAINER_LENGTH
        error = structure_error(number)
        assert error is not None
        assert error.code == "not_uppercase_letter"
        assert error.position == 1


def test_correction_candidates_with_unpaired_surrogate_first_position() -> None:
    # 首位为未配对代理字符的 11 位箱号：候选枚举照常完整生成，
    # 差异位置 1 的候选原样携带该代理字符作为原字符。
    number = "\ud800SQU3054383"
    candidates = correction_candidates(number)
    assert candidates
    assert all(c.container_number != number for c in candidates)
    position_one = [c for c in candidates if c.position == 1]
    assert position_one
    assert all(c.original_character == "\ud800" for c in position_one)
    # 每个候选结构合法且校验位通过（与枚举口径一致）。
    for candidate in candidates:
        assert structure_error(candidate.container_number) is None
        assert expected_check_digit(candidate.container_number[:10]) == int(
            candidate.container_number[10]
        )


# ---------------------------------------------------------------- 清单核对

# 经独立参考实现（本文件上方 _reference_letter_values）确认合法的箱号：
# CSQU3054383（余 3）、AAAU0000060（余 10 折 0）、BBBU0000000（余 10 折 0）、
# ABCU1234560（余 0）、MSCU6355895（余 5）。
A = "CSQU3054383"
B = "AAAU0000060"
C = "BBBU0000000"
D = "ABCU1234560"
E = "MSCU6355895"

# 结构合法但校验位不符的箱号（前 10 位与 A 相同，末位错为 4）。
A_BAD = "CSQU3054384"


def _reference_pairing(expected: list[str], onsite: list[str]) -> dict:
    """测试侧独立参考配对实现：对每个完整箱号按出现次序对齐。

    与服务端“现场侧建 FIFO 队列、预期侧逐项消费”的实现刻意不同：
    收集两侧每个箱号的索引列表，zip 对齐得到配对，各自剩余的即差异。
    """
    expected_positions: dict[str, list[int]] = {}
    onsite_positions: dict[str, list[int]] = {}
    for i, n in enumerate(expected):
        expected_positions.setdefault(n, []).append(i)
    for i, n in enumerate(onsite):
        onsite_positions.setdefault(n, []).append(i)

    matched: list[tuple[str, int, int]] = []
    missing: list[tuple[str, int]] = []
    extra: list[tuple[str, int]] = []
    for number, epos in expected_positions.items():
        opos = onsite_positions.get(number, [])
        k = min(len(epos), len(opos))
        matched.extend((number, e, o) for e, o in zip(epos[:k], opos[:k]))
        missing.extend((number, i) for i in epos[k:])
    for number, opos in onsite_positions.items():
        epos = expected_positions.get(number, [])
        extra.extend((number, i) for i in opos[len(epos) :])

    # 服务端口径：matched/missing 按预期顺序，extra 按现场顺序。
    matched.sort(key=lambda t: t[1])
    missing.sort(key=lambda t: t[1])
    extra.sort(key=lambda t: t[1])
    return {"matched": matched, "missing": missing, "extra": extra}


def _assert_conservation(
    expected: list[str], onsite: list[str], result: ReconcileResult
) -> None:
    """配对数量守恒与计数一致性。"""
    assert result.expected_count == len(expected)
    assert result.onsite_count == len(onsite)
    assert result.matched_count == len(result.matched)
    assert result.missing_count == len(result.missing)
    assert result.extra_count == len(result.extra)
    # 每条预期项要么配对要么缺少，每条现场项要么配对要么多出。
    assert result.matched_count + result.missing_count == len(expected)
    assert result.matched_count + result.extra_count == len(onsite)
    # 每个箱号的配对数不超过任一侧的出现次数。
    e_counts = {n: expected.count(n) for n in expected}
    o_counts = {n: onsite.count(n) for n in onsite}
    pair_counts: dict[str, int] = {}
    for item in result.matched:
        pair_counts[item.container_number] = (
            pair_counts.get(item.container_number, 0) + 1
        )
    for number, count in pair_counts.items():
        assert count <= e_counts[number]
        assert count <= o_counts[number]


def test_reconcile_identical_in_order_lists() -> None:
    # 完全一致（顺序相同）：全部配对，无差异。
    expected = [A, B, C]
    result = reconcile_container_numbers(expected, [A, B, C])
    assert isinstance(result, ReconcileResult)
    assert (result.matched_count, result.missing_count, result.extra_count) == (
        3,
        0,
        0,
    )
    assert [(m.container_number, m.expected_index, m.onsite_index) for m in result.matched] == [
        (A, 0, 0),
        (B, 1, 1),
        (C, 2, 2),
    ]
    assert result.missing == ()
    assert result.extra == ()
    _assert_conservation(expected, [A, B, C], result)


def test_reconcile_identical_shuffled_lists_pair_in_fifo_occurrence() -> None:
    # 完全一致但乱序：仍全部配对；每个箱号按各自清单中的出现次序对齐。
    expected = [A, B, A, C, B]
    onsite = [B, A, C, A, B]
    result = reconcile_container_numbers(expected, onsite)
    assert isinstance(result, ReconcileResult)
    assert result.matched_count == 5
    assert result.missing_count == result.extra_count == 0
    pairs = [(m.container_number, m.expected_index, m.onsite_index) for m in result.matched]
    # matched 按预期清单顺序。
    assert [p[1] for p in pairs] == [0, 1, 2, 3, 4]
    # 同一箱号按出现次序 FIFO 对齐：A 的第 1/2 次配现场第 1/2 次，以此类推。
    a_pairs = sorted((e, o) for n, e, o in pairs if n == A)
    assert a_pairs == [(0, 1), (2, 3)]
    b_pairs = sorted((e, o) for n, e, o in pairs if n == B)
    assert b_pairs == [(1, 0), (4, 4)]
    assert [(n, e, o) for n, e, o in pairs if n == C] == [(C, 3, 2)]
    _assert_conservation(expected, onsite, result)
    assert pairs == [tuple(x) for x in _reference_pairing(expected, onsite)["matched"]]


def test_reconcile_single_sided_missing_and_extra() -> None:
    # 单侧缺失 + 现场多出：missing 按预期顺序，extra 按现场顺序。
    expected = [A, B, C]
    onsite = [D, A, B]  # C 缺失；D 多出
    result = reconcile_container_numbers(expected, onsite)
    assert isinstance(result, ReconcileResult)
    assert (result.matched_count, result.missing_count, result.extra_count) == (
        2,
        1,
        1,
    )
    assert [(m.container_number, m.expected_index, m.onsite_index) for m in result.matched] == [
        (A, 0, 1),
        (B, 1, 2),
    ]
    assert [(m.container_number, m.expected_index) for m in result.missing] == [(C, 2)]
    assert [(x.container_number, x.onsite_index) for x in result.extra] == [(D, 0)]
    _assert_conservation(expected, onsite, result)


def test_reconcile_missing_and_extra_preserve_list_order() -> None:
    # 乱序且多项缺失/多出：结果严格保持各自清单顺序，并与独立参考一致。
    expected = [C, A, D, B, E]
    onsite = [E, B, D, A, D]  # 缺 C；D 现场 2 次/预期 1 次；E、A、B 配对
    result = reconcile_container_numbers(expected, onsite)
    assert isinstance(result, ReconcileResult)
    reference = _reference_pairing(expected, onsite)
    assert [(m.container_number, m.expected_index, m.onsite_index) for m in result.matched] == reference["matched"]
    assert [(m.container_number, m.expected_index) for m in result.missing] == reference["missing"]
    assert [(x.container_number, x.onsite_index) for x in result.extra] == reference["extra"]
    # missing 按预期顺序：C(2) 在前；extra 按现场顺序：只有多出的 D(4)。
    assert [(m.container_number, m.expected_index) for m in result.missing] == [
        (C, 0)
    ]
    assert [(x.container_number, x.onsite_index) for x in result.extra] == [(D, 4)]
    _assert_conservation(expected, onsite, result)


def test_reconcile_duplicate_count_mismatch_only_last_occurrence_differs() -> None:
    # 同一箱号一侧三次、另一侧两次：前两次配对，只有最后一次归入差异。
    # 预期侧 3 次、现场侧 2 次 -> 最后一次（预期索引 3）为 missing。
    expected = [A, B, A, A]
    onsite = [A, A, B]
    result = reconcile_container_numbers(expected, onsite)
    assert isinstance(result, ReconcileResult)
    assert result.matched_count == 3
    assert [(m.container_number, m.expected_index, m.onsite_index) for m in result.matched] == [
        (A, 0, 0),
        (B, 1, 2),
        (A, 2, 1),
    ]
    assert [(m.container_number, m.expected_index) for m in result.missing] == [(A, 3)]
    assert result.extra == ()
    _assert_conservation(expected, onsite, result)

    # 现场侧 3 次、预期侧 2 次 -> 最后一次（现场索引 2）为 extra。
    expected = [B, A]
    onsite = [A, B, A]
    result = reconcile_container_numbers(expected, onsite)
    assert isinstance(result, ReconcileResult)
    assert result.matched_count == 2
    assert result.missing == ()
    assert [(x.container_number, x.onsite_index) for x in result.extra] == [(A, 2)]
    _assert_conservation(expected, onsite, result)


def test_reconcile_repeated_numbers_conservation_across_shuffled_samples() -> None:
    # 乱序 + 重复的组合样例：与独立参考逐字段一致，且配对数量守恒。
    samples = [
        ([A, A, B], [B, A, A]),
        ([A, B, A, B, C], [C, B, A, B, A]),
        ([A, A, A], [A, A]),
        ([A, A], [A, A, A]),
        ([A, B, C, A], [A, A, B, C]),
        ([E, E, D, E, D], [D, E, D, E, E]),
        ([A, B, C, D, E], [E, D, C, B, A]),
    ]
    for expected, onsite in samples:
        result = reconcile_container_numbers(expected, onsite)
        assert isinstance(result, ReconcileResult), (expected, onsite)
        reference = _reference_pairing(expected, onsite)
        pairs = [(m.container_number, m.expected_index, m.onsite_index) for m in result.matched]
        miss = [(m.container_number, m.expected_index) for m in result.missing]
        extra = [(x.container_number, x.onsite_index) for x in result.extra]
        assert pairs == reference["matched"], (expected, onsite)
        assert miss == reference["missing"], (expected, onsite)
        assert extra == reference["extra"], (expected, onsite)
        _assert_conservation(expected, onsite, result)


def test_reconcile_no_normalization_distinct_strings_never_pair() -> None:
    # 不做大小写或空白归一化：合法号与视觉相近的串即使结构/校验不同，
    # 也不会被当作同一完整箱号配对（这里小写号结构非法，整次核对拒绝）。
    result = reconcile_container_numbers([A], ["csqu3054383"])
    assert isinstance(result, ReconcileInvalidItem)
    assert result.list_source == ONSITE_LIST
    assert result.index == 0


def test_reconcile_structure_invalid_in_expected_list_rejected() -> None:
    # 预期清单含结构非法项：拒绝，来源 expected、最小索引、首个损坏位置。
    result = reconcile_container_numbers([A, "csqu3054383"], [A])
    assert isinstance(result, ReconcileInvalidItem)
    assert result.list_source == EXPECTED_LIST
    assert result.index == 1
    assert result.container_number == "csqu3054383"
    assert result.structure_error is not None
    assert result.structure_error.code == "not_uppercase_letter"
    assert result.structure_error.position == 1
    assert result.expected_check_digit is None
    assert result.actual_check_digit is None
    assert result.passed is False


def test_reconcile_structure_invalid_in_onsite_list_rejected() -> None:
    # 现场清单含结构非法项（预期清单已全部有效）：来源 onsite。
    result = reconcile_container_numbers([A], [A, "CSQX3054383"])
    assert isinstance(result, ReconcileInvalidItem)
    assert result.list_source == ONSITE_LIST
    assert result.index == 1
    assert result.structure_error is not None
    assert result.structure_error.code == "invalid_category_identifier"
    assert result.structure_error.position == 4
    assert result.expected_check_digit is None
    assert result.actual_check_digit is None


def test_reconcile_expected_list_checked_before_onsite_list() -> None:
    # 两侧都有无效项时先报预期清单（即便现场侧索引更小）。
    result = reconcile_container_numbers(
        [A, "CSQX3054383"], ["!!", A]
    )
    assert isinstance(result, ReconcileInvalidItem)
    assert result.list_source == EXPECTED_LIST
    assert result.index == 1


def test_reconcile_minimum_invalid_index_within_list() -> None:
    # 清单内多个无效项：报最小索引；合法项在前不影响定位。
    result = reconcile_container_numbers([A, "!!", "csqu3054383"], [A])
    assert isinstance(result, ReconcileInvalidItem)
    assert result.list_source == EXPECTED_LIST
    assert result.index == 1
    assert result.structure_error is not None
    assert result.structure_error.code == "invalid_length"
    assert result.structure_error.position == 3


def test_reconcile_check_digit_mismatch_rejected_with_original_verdict() -> None:
    # 结构合法但校验位不符：同样拒绝；结构字段为空，携带期望/实际校验位。
    result = reconcile_container_numbers([A], [A_BAD])
    assert isinstance(result, ReconcileInvalidItem)
    assert result.list_source == ONSITE_LIST
    assert result.index == 0
    assert result.container_number == A_BAD
    assert result.structure_error is None
    assert result.expected_check_digit == 3
    assert result.actual_check_digit == 4
    assert result.passed is False


def test_reconcile_check_digit_mismatch_in_expected_list() -> None:
    # 预期清单中的校验位不符同样拒绝（当班作业清单也必须先过校验）。
    result = reconcile_container_numbers([A_BAD], [A])
    assert isinstance(result, ReconcileInvalidItem)
    assert result.list_source == EXPECTED_LIST
    assert result.structure_error is None
    assert (result.expected_check_digit, result.actual_check_digit) == (3, 4)


def test_reconcile_invalid_returns_no_pairing_payload() -> None:
    # 拒绝时返回类型是 ReconcileInvalidItem，而不是携带任何配对结果。
    result = reconcile_container_numbers([A, "CSQU305438A"], [A])
    assert isinstance(result, ReconcileInvalidItem)
    assert not isinstance(result, ReconcileResult)


def test_reconcile_results_are_immutable() -> None:
    result = reconcile_container_numbers([A], [A])
    assert isinstance(result, ReconcileResult)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.matched_count = 0  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.matched[0].container_number = ""  # type: ignore[misc]


def test_reconcile_empty_valid_lists_when_called_directly() -> None:
    # 领域函数本身不限制清单长度（长度 1..100 由契约层保证）：
    # 两份空清单是一次无差异的合法核对。
    result = reconcile_container_numbers([], [])
    assert isinstance(result, ReconcileResult)
    assert (result.expected_count, result.onsite_count) == (0, 0)
    assert (result.matched_count, result.missing_count, result.extra_count) == (
        0,
        0,
        0,
    )
    assert result.matched == result.missing == result.extra == ()


# ---------------------------------------------------------------- 多读数共识

# 经独立参考实现确认合法的箱号：CSQU3054383（余 3）、CSQU3054399（余 9）。
CONS_A = "CSQU3054383"
CONS_B = "CSQU3054399"


def _reference_consensus(readings: list[str]) -> tuple[int, int, list[str]] | None:
    """测试侧独立参考实现：候选域笛卡尔积暴力枚举，逐组合复算校验位与
    逐位不一致总数，与服务端的反向动态规划实现刻意不同。

    返回 (最小代价, 最优解总数, 字典序解列表)；无解时返回 None。
    """
    domains = (
        [set(string.ascii_uppercase)] * 3
        + [set("UJZ")]
        + [set(string.digits)] * 7
    )
    candidates: list[list[str]] = []
    for position in range(CONTAINER_LENGTH):
        observed = {reading[position] for reading in readings}
        candidates.append(sorted(observed & domains[position]))
    best_cost: int | None = None
    solutions: list[str] = []
    for combo in itertools.product(*candidates):
        remainder = _reference_weighted_sum("".join(combo[:10])) % MODULUS
        if (0 if remainder == 10 else remainder) != int(combo[10]):
            continue
        cost = sum(
            reading[position] != combo[position]
            for reading in readings
            for position in range(CONTAINER_LENGTH)
        )
        if best_cost is None or cost < best_cost:
            best_cost = cost
            solutions = ["".join(combo)]
        elif cost == best_cost:
            solutions.append("".join(combo))
    if best_cost is None:
        return None
    return best_cost, len(solutions), sorted(solutions)


# 与 test_api.py 同形的独立加权和参考（本文件顶部已有字母表参考实现）。
MODULUS = 11


def _reference_weighted_sum(first_ten: str) -> int:
    values = _reference_letter_values()
    return sum(
        (int(char) if char.isdigit() else values[char]) * 2**position
        for position, char in enumerate(first_ten)
    )


def _assert_matches_reference(readings: list[str], result: ConsensusResult) -> None:
    """共识结果与独立暴力枚举逐字段一致（全局最优、精确计数、字典序）。"""
    reference = _reference_consensus(readings)
    assert result.reading_count == len(readings)
    if reference is None:
        assert result.minimum_cost is None
        assert result.optimal_count == 0
        assert result.solutions == ()
        assert result.truncated is False
        return
    cost, count, numbers = reference
    assert result.minimum_cost == cost
    assert result.optimal_count == count
    assert result.solutions == tuple(numbers[:CONSENSUS_SOLUTION_LIMIT])
    assert result.truncated is (count > CONSENSUS_SOLUTION_LIMIT)
    # 每个返回解自身结构合法、校验位通过，且代价确为最小代价。
    for number in result.solutions:
        assert structure_error(number) is None
        assert expected_check_digit(number[:10]) == int(number[10])
        assert (
            sum(
                reading[position] != number[position]
                for reading in readings
                for position in range(CONTAINER_LENGTH)
            )
            == cost
        )


def test_consensus_unique_solution_with_duplicates_counted() -> None:
    # 重复读数重复计票：两条正确读数 + 一条末位抄错读数，唯一共识。
    result = consensus_container_number([CONS_A, CONS_A, "CSQU3054384"])
    assert result.minimum_cost == 1
    assert result.optimal_count == 1
    assert result.solutions == (CONS_A,)
    assert result.truncated is False
    _assert_matches_reference([CONS_A, CONS_A, "CSQU3054384"], result)


def test_consensus_global_optimum_defeats_local_majority() -> None:
    # 局部多数违反校验位：逐位多数拼装为 "CSQU3054389"（第 10 位 8 占 3
    # 票、末位 9 占 4 票），其校验位不符；即使把末位修补为期望的 3，
    # 代价也是 6。全局最优反而把第 10 位翻成少数派 9：CSQU3054399，
    # 代价 4——绝不先逐位取多数再修补末位。
    readings = [CONS_A, "CSQU3054389", CONS_B, CONS_B, "CSQU3054389"]
    result = consensus_container_number(readings)
    assert result.minimum_cost == 4
    assert result.optimal_count == 1
    assert result.solutions == (CONS_B,)
    # 逐位多数拼装号本身校验位不符（前缀余 3，多数末位为 9）。
    assert expected_check_digit("CSQU305438") == 3
    assert expected_check_digit("CSQU305439") == 9
    _assert_matches_reference(readings, result)


def test_consensus_over_hundred_tied_optima_truncated_in_lexicographic_order() -> None:
    # 两条读数在 10 个位置上各持一个合法字符：每个满足校验位的组合代价
    # 恒为 10 且全部并列最优，最优解总数（独立枚举为 139）超过 100。
    readings = ["AAAU0000000", "BBBU1111111"]
    result = consensus_container_number(readings)
    assert result.minimum_cost == 10
    assert result.optimal_count == 139 > CONSENSUS_SOLUTION_LIMIT
    assert len(result.solutions) == CONSENSUS_SOLUTION_LIMIT
    assert result.truncated is True
    # 前一百个解按完整箱号字典序，且与独立暴力枚举完全一致。
    assert list(result.solutions) == sorted(result.solutions)
    _assert_matches_reference(readings, result)


def test_consensus_no_solution_when_position_has_no_legal_observation() -> None:
    # 第一类无解边界：第 4 位（类别码）全部观测都是非法字符，候选域为空。
    result = consensus_container_number(["CSQX3054383", "CSQD3054384"])
    assert result.minimum_cost is None
    assert result.optimal_count == 0
    assert result.solutions == ()
    assert result.truncated is False
    _assert_matches_reference(["CSQX3054383", "CSQD3054384"], result)


def test_consensus_no_solution_when_no_combination_satisfies_check_digit() -> None:
    # 第二类无解边界：候选域非空，但唯一可行前缀的期望校验位是 3，
    # 而末位候选域只有 {4, 5}。
    readings = ["CSQU3054384", "CSQU3054385"]
    result = consensus_container_number(readings)
    assert result.minimum_cost is None
    assert result.optimal_count == 0
    assert result.solutions == ()
    assert result.truncated is False
    _assert_matches_reference(readings, result)


def test_consensus_illegal_characters_only_count_as_disagreement() -> None:
    # 非法字符只计不一致、不进入候选域：小写读数不会把小写带进解里，
    # 共识仍为大写合法号；位置 1-4 各计 1 次不一致。
    readings = [CONS_A, "csqu3054383"]
    result = consensus_container_number(readings)
    assert result.minimum_cost == 4
    assert result.optimal_count == 1
    assert result.solutions == (CONS_A,)
    _assert_matches_reference(readings, result)


def test_consensus_ambiguous_optima_sorted_by_full_number() -> None:
    # 两个合法号互为一位之差且各一票：两个最优解并列，按字典序返回。
    result = consensus_container_number([CONS_B, CONS_A])  # 输入乱序不影响解序
    assert result.minimum_cost == 2
    assert result.optimal_count == 2
    assert result.solutions == (CONS_A, CONS_B)  # 字典序，非输入顺序
    assert result.truncated is False
    _assert_matches_reference([CONS_B, CONS_A], result)


def test_consensus_requires_exact_length_for_every_reading() -> None:
    for bad in ("CSQU305438", "CSQU30543834", "", " CSQU3054383"):
        with pytest.raises(ValueError):
            consensus_container_number([CONS_A, bad])


def test_consensus_result_is_immutable() -> None:
    result = consensus_container_number([CONS_A, CONS_A])
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.minimum_cost = 0  # type: ignore[misc]


def test_consensus_matches_brute_force_on_random_readings() -> None:
    # 小字符域随机读数（含非法字符与重复读数）：全局最优代价、精确计数、
    # 字典序解序列与独立暴力枚举逐字段一致。
    rng = random.Random(20260915)
    letter_pool = ["A", "B", "C", "a", "!"]
    category_pool = ["U", "J", "X", "u"]
    digit_pool = ["0", "1", "2", "x"]
    for _ in range(40):
        readings = [
            "".join(
                rng.choice(letter_pool)
                if position < 3
                else rng.choice(category_pool)
                if position == 3
                else rng.choice(digit_pool)
                for position in range(CONTAINER_LENGTH)
            )
            for _ in range(rng.randint(2, 4))
        ]
        _assert_matches_reference(readings, consensus_container_number(readings))
